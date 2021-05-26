# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Lint as: python3
"""Retrieve an existing Falken object from data_store."""

from absl import logging
from api import proto_conversion
from data_store import resource_id

# pylint: disable=g-bad-import-order
import common.generate_protos  # pylint: disable=unused-import
from google.rpc import code_pb2
import falken_service_pb2


class GetHandler:
  """Handles retrieving of a Falken proto instance for a given request.

  This is a base class extended by implementation handlers for each type of
  request.
  """

  def __init__(self, request, context, data_store, request_args,
               glob_pattern):
    """Initialize GetHandler.

    Args:
      request: falken_service_pb2.Get*Request containing fields that are used
        to query the Falken object.
      context: grpc.ServicerContext containing context about the RPC.
      data_store: Falken data_store.DataStore object to read the Falken object
        from.
      request_args: list of field names of the request, ordered in
        FALKEN_RESOURCE_SPEC in data_store/resource_id.py
      glob_pattern: String used in combination with request_args to generate the
        resource_id.FalkenResourceId to query the data_store.
    """
    self._request = request
    self._context = context
    self._request_type = type(request)
    self._data_store = data_store
    self._glob_pattern = glob_pattern
    self._request_args = request_args

  def get(self):
    """Retrieves the instance requested from data store.

    Returns:
      session: Falken proto that was requested.
    """
    logging.debug('Get called with request %s', str(self._request))

    return self._read_and_convert_proto(
        resource_id.FalkenResourceId(self._instantiate_glob_pattern()))

  def _read_and_convert_proto(self, res_id):
    return proto_conversion.ProtoConverter.convert_proto(
        self._data_store.read(res_id))

  def _instantiate_glob_pattern(self):
    """Instantiates glob pattern using request attributes.

    Returns:
      A string containing a glob pattern that can be used to find or list
      resources.
    Raises:
      Exception: The gRPC context is aborted when the required fields are not
        specified in the request, which raises an exception to terminate the RPC
        with a no-OK status.
    """
    args = []
    for arg in self._request_args:
      try:
        attr = getattr(self._request, arg)
      except AttributeError:
        attr = None

      if not attr:
        self._context.abort(
            code_pb2.INVALID_ARGUMENT,
            f'Could not find {arg} in {self._request_type}.')
      args.append(attr)

    return self._glob_pattern.format(*args)


class GetBrainHandler(GetHandler):
  """Handles retrieving an existing brain."""

  def __init__(self, request, context, data_store):
    super().__init__(request, context, data_store, ['project_id', 'brain_id'],
                     'projects/{0}/brains/{1}')


class GetSessionHandler(GetHandler):
  """Handles retrieving an existing session."""

  def __init__(self, request, context, data_store):
    super().__init__(
        request, context, data_store, ['project_id', 'brain_id', 'session_id'],
        'projects/{0}/brains/{1}/sessions/{2}')


class GetSessionByIndexHandler(GetHandler):
  """Handles retrieving an existing session by index."""

  def __init__(self, request, context, data_store):
    super().__init__(request, context, data_store, ['project_id', 'brain_id'],
                     'projects/{0}/brains/{1}/sessions/*')

  def get(self):
    logging.debug(
        'GetSessionByIndex called for project_id %s with brain_id %s and index '
        '%d.', self._request.project_id, self._request.brain_id,
        self._request.session_index)
    if not (self._request.project_id and self._request.brain_id and
            self._request.session_index):
      self._context.abort(
          code_pb2.INVALID_ARGUMENT,
          'Project ID, brain ID, and session index must be specified in '
          'GetSessionByIndexRequest.')

    session_ids, _ = self._data_store.list(
        resource_id.FalkenResourceId(
            self._glob_pattern.format(
                self._request.project_id, self._request.brain_id)),
        page_size=self._request.session_index + 1)
    if len(session_ids) < self._request.session_index:
      self._context.abort(
          code_pb2.INVALID_ARGUMENT,
          f'Session at index {self._request.session_index} was not found.')

    return self._read_and_convert_proto(
        session_ids[self._request.session_index])


class GetModelHandler(GetHandler):
  """Handles retrieving an existing model."""

  def __init__(self, request, context, data_store):
    if request.snapshot_id and request.model_id:
      context.abort(
          code_pb2.INVALID_ARGUMENT,
          'Either model ID or snapshot ID should be specified, not both. '
          f'Found snapshot_id {request.snapshot_id} and model_id '
          f'{request.model_id}.')

    if request.snapshot_id:
      super().__init__(request, context, data_store,
                       ['project_id', 'brain_id', 'snapshot_id'],
                       'projects/{0}/brains/{1}/snapshots/{2}')
    else:
      super().__init__(request, context, data_store,
                       ['project_id', 'brain_id', 'model_id'],
                       'projects/{0}/brains/{1}/sessions/*/models/{2}')

  def get(self):
    glob = self._instantiate_glob_pattern()

    if self._request.model_id:
      listed_ids, _ = self._data_store.list(
          resource_id.FalkenResourceId(glob),
          page_size=2)

      if not listed_ids:
        self._context.abort(
            code_pb2.INVALID_ARGUMENT, 'No models found for the given request.')

      if len(listed_ids) > 1:
        raise RuntimeError(f'{len(listed_ids)} resources found for glob '
                           f'{glob}, but only one was expected.')

      model_id = listed_ids[0]
    else:
      snapshot = self._read_and_convert_proto(
          resource_id.FalkenResourceId(glob))
      model_id = resource_id.FalkenResourceId(
          project=snapshot.project_id,
          brain=snapshot.brain_id,
          session=snapshot.session,
          model=snapshot.model)

    unused_serialized_model = self._read_and_convert_proto(
        resource_id.FalkenResourceId(
            project=model_id.project,
            brain=model_id.brain,
            session=model_id.session,
            model=model_id.model,
            attribute='serialized_model'))

    # TODO(b/189117004): Fill in model_response.serialized_model with a
    # compressed version of the model in unused_serialized_model.
    return falken_service_pb2.Model(model_id=model_id.model)
