# Copyright (C) 2016  Red Hat, Inc
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Etcd based StoreHandler.
"""

import json

import etcd

from urllib.parse import urlparse

import commissaire.models as models

from commissaire.bus import StorageLookupError
from commissaire.util.config import etcd_client_args
from commissaire.storage import StoreHandlerBase, ConfigurationError

#: Maps ModelClassName to a key pattern
_etcd_mapper = {
    'Cluster': '/clusters/{}',
    'ClusterDeploy': '/cluster/{}/deploy',
    'ClusterRestart': '/cluster/{}/restart',
    'ClusterUpgrade': '/cluster/{}/upgrade',
    'Clusters': '/clusters/',
    'ContainerManagerConfig': '/container_managers/{}',
    'ContainerManagerConfigs': '/container_managers/',
    'Host': '/hosts/{}',
    'HostCreds': '/hosts/{}',  # XXX Temporary hack
    'Hosts': '/hosts',
    'Network': '/networks/{}',
    'Networks': '/networks',
    'Status': '/status',
}


class EtcdStoreHandler(StoreHandlerBase):
    """
    Handler for data storage on etcd.
    """

    DEFAULT_SERVER_URL = 'http://127.0.0.1:2379'

    @classmethod
    def check_config(cls, config):
        """
        Examines the configuration parameters for an EtcdStoreHandler
        and throws a ConfigurationError if any parameters are invalid.
        """
        url = urlparse(config.get('server_url', cls.DEFAULT_SERVER_URL))
        if (bool(config.get('certificate_path')) ^
                bool(config.get('certificate_key_path'))):
            raise ConfigurationError(
                'Both "certificate_path" and "certificate_key_path" '
                'must be provided to use a client side certificate')
        if config.get('certificate-path'):
            if url.scheme != 'https':
                raise ConfigurationError(
                    'Server URL scheme must be "https" when using client '
                    'side certificates (got "{}")'.format(url.scheme))

    def __init__(self, config):
        """
        Creates a new instance of EtcdStoreHandler.

        :param config: Configuration details
        :type config: dict
        """
        super().__init__(config)

        # Seed the configuration with environment variables.
        client_args = etcd_client_args()

        # The server_url in the JSON config overrides environment
        # variables.  If neither are specified, use a default URL.
        if 'server_url' in config:
            url = urlparse(config['server_url'])
        elif 'host' not in client_args:
            url = urlparse(self.DEFAULT_SERVER_URL)
        else:
            url = None

        if url:
            client_args = {
                'host': url.hostname,
                'protocol': url.scheme
            }
            if url.port is not None:
                client_args['port'] = url.port

        if 'certificate_path' in config:
            client_args['cert'] = (
                config['certificate_path'],
                config['certificate_key_path'])
        if 'certificate_ca_path' in config:
            client_args['ca_cert'] = config['certificate_ca_path']
        self._store = etcd.Client(**client_args)
        self._etcd_namespace = '/commissaire'

    def _format_key(self, model_instance):
        """
        Takes a model instance and figures out its key.

        :param model_instance: Model instance to save
        :type model_instance: commissaire.model.Model
        :returns: The etcd key
        :rtype: str
        """
        subkey = _etcd_mapper[model_instance.__class__.__name__]
        if model_instance._primary_key:
            subkey = subkey.format(
                getattr(model_instance, model_instance._primary_key))
        return self._etcd_namespace + subkey

    def _save(self, model_instance):
        """
        Saves data to etcd and returns back a saved model.

        :param model_instance: Model instance to save
        :type model_instance: commissaire.model.Model
        :returns: The saved model instance
        :rtype: commissaire.model.Model
        """
        key = self._format_key(model_instance)
        json_string = model_instance.to_json()
        # XXX Temporary hack to merge Host and HostCreds models
        #     to the same etcd key.
        if (isinstance(model_instance, models.HostCreds) or
                isinstance(model_instance, models.Host)):
            try:
                merge_dict = json.loads(self._store.get(key).value)
                final_dict = json.loads(json_string)
                final_dict.update(merge_dict)
                json_string = json.dumps(final_dict)
            except etcd.EtcdKeyNotFound:
                pass
        etcd_resp = self._store.write(key, json_string)

        # XXX For HostCreds just return what was passed in, with no
        #     notifications.
        if not isinstance(model_instance, models.HostCreds):
            model_type = type(model_instance)
            model_dict = json.loads(etcd_resp.value)
            model_instance = model_type.new(**model_dict)
            if etcd_resp.newKey:
                self.notify.created(model_instance)
            else:
                self.notify.updated(model_instance)

        return model_instance

    def _get(self, model_instance):
        """
        Returns data from a store and returns back a model.

        :param model_instance: Model instance to search and return
        :type model_instance: commissaire.model.Model
        :returns: The model instance
        :rtype: commissaire.model.Model
        :raises StorageLookupError: if data lookup fails
        """
        try:
            key = self._format_key(model_instance)
            etcd_resp = self._store.get(key)
            json_value = json.loads(etcd_resp.value)
            # XXX Temporary hack to create Host and HostCreds models
            #     from the same etcd key.
            if isinstance(model_instance, models.HostCreds):
                new_json_value = {'address': json_value.get('address')}
                if 'ssh_priv_key' in json_value:
                    new_json_value['ssh_priv_key'] = json_value['ssh_priv_key']
                if 'remote_user' in json_value:
                    new_json_value['remote_user'] = json_value['remote_user']
                json_value = new_json_value
            return model_instance.new(**json_value)
        except etcd.EtcdKeyNotFound as error:
            raise StorageLookupError(str(error), model_instance)

    def _delete(self, model_instance):
        """
        Deletes data from a store.

        :param model_instance: Model instance to delete
        :type model_instance: commissaire.model.Model
        :raises StorageLookupError: if data lookup fails
        """
        try:
            key = self._format_key(model_instance)
            etcd_resp = self._store.delete(key)

            model_type = type(model_instance)
            model_dict = json.loads(etcd_resp._prev_node.value)
            model_instance = model_type.new(**model_dict)
            self.notify.deleted(model_instance)
        except etcd.EtcdKeyNotFound as error:
            raise StorageLookupError(str(error), model_instance)

    def _list(self, model_instance):
        """
        Lists data at a location in a store and returns back model instances.

        :param model_instance: Model instance to search for and list
        :type model_instance: commissaire.model.ListModel
        :returns: A list of models
        :rtype: list
        """
        key = self._format_key(model_instance)
        model_cls = model_instance._list_class
        results = []

        # populate the results
        for item in self._store.read(key, recursive=True).children:
            try:
                results.append(model_cls(**json.loads(item.value)))
            except TypeError as error:
                # XXX: This usually means there were not results. it's
                #      possible that bad data could trigger a response.
                self.logger.warn(
                    'Etcd returned unserializable data. Skipping the record.'
                    'TypeError: %s', error)

        # Fill the list container with the results and return the model.
        setattr(model_instance, model_instance._list_attr, results)

        return model_instance


PluginClass = EtcdStoreHandler
