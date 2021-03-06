#!/usr/bin/env python3
# -*- mode: python; coding: utf-8 -*-

import logging
from datetime import datetime, timedelta
import sys
import requests
from requests.compat import urljoin
from requests_oauthlib import OAuth1Session
from threading import RLock

sys.version_info >= (3, 0) or exit('Python 3 required')

__version__ = '0.10.4'

_LOGGER = logging.getLogger(__name__)

TELLDUS_LIVE_API_URL = 'https://api.telldus.com/json/'
TELLDUS_LIVE_REQUEST_TOKEN_URL = 'https://api.telldus.com/oauth/requestToken'
TELLDUS_LIVE_AUTHORIZE_URL = 'https://api.telldus.com/oauth/authorize'
TELLDUS_LIVE_ACCESS_TOKEN_URL = 'https://api.telldus.com/oauth/accessToken'

TELLDUS_LOCAL_API_URL = 'http://{host}/api/'
TELLDUS_LOCAL_REQUEST_TOKEN_URL = 'http://{host}/api/token'
TELLDUS_LOCAL_REFRESH_TOKEN_URL = 'http://{host}/api/refreshToken'

TIMEOUT = timedelta(seconds=10)

UNNAMED_DEVICE = 'NO NAME'

# Tellstick methods
# pylint:disable=invalid-name
TURNON = 1
TURNOFF = 2
BELL = 4
TOGGLE = 8
DIM = 16
LEARN = 32
UP = 128
DOWN = 256
STOP = 512
RGBW = 1024
THERMOSTAT = 2048

SUPPORTED_METHODS = (
    TURNON |
    TURNOFF |
    DIM |
    UP |
    DOWN |
    STOP)

METHODS = {
    TURNON: 'turnOn',
    TURNOFF: 'turnOff',
    BELL: 'bell',
    TOGGLE: 'toggle',
    DIM: 'dim',
    LEARN: 'learn',
    UP: 'up',
    DOWN: 'down',
    STOP: 'stop',
    RGBW: 'rgbw',
    THERMOSTAT: 'thermostat'
}

# Sensor types
TEMPERATURE = 'temperature'
HUMIDITY = 'humidity'
RAINRATE = 'rrate'
RAINTOTAL = 'rtot'
WINDDIRECTION = 'wdir'
WINDAVERAGE = 'wavg'
WINDGUST = 'wgust'
UV = 'uv'
WATT = 'watt'
LUMINANCE = 'lum'
DEW_POINT = 'dewp'
BAROMETRIC_PRESSURE = 'barpress'

BATTERY_LOW = 255
BATTERY_UNKNOWN = 254
BATTERY_OK = 253

SUPPORTS_LOCAL_API = ['TellstickZnet', 'TellstickNetV2']


def supports_local_api(device):
    """Return true if the device supports local access."""
    return any(dev in device
               for dev in SUPPORTS_LOCAL_API)


class LocalAPISession(requests.Session):
    """Connect directly to the device."""

    def __init__(self, host, application, access_token=None):
        super().__init__()
        self.url = TELLDUS_LOCAL_API_URL.format(host=host)
        self._host = host
        self._application = application
        self.request_token = None
        self.token_timestamp = None
        self.access_token = access_token
        if access_token:
            self.headers.update(
                {'Authorization': 'Bearer {}'.format(self.access_token)})
            self.refresh_access_token()

    @property
    def authorize_url(self):
        """Retrieve URL for authorization."""
        try:
            response = self.put(
                TELLDUS_LOCAL_REQUEST_TOKEN_URL.format(host=self._host),
                data={'app': self._application},
                timeout=TIMEOUT.seconds)
            response.raise_for_status()
            result = response.json()
            self.request_token = result.get('token')
            return result.get('authUrl')
        except (OSError, ValueError) as e:
            _LOGGER.error('Failed to retrieve authorization URL: %s', e)

    def authorize(self):
        """Perform authorization."""
        try:
            response = self.get(
                TELLDUS_LOCAL_REQUEST_TOKEN_URL.format(host=self._host),
                params=dict(token=self.request_token),
                timeout=TIMEOUT.seconds)
            response.raise_for_status()
            result = response.json()
            if 'token' in result:
                self.access_token = result['token']
                self.headers.update(
                    {'Authorization': 'Bearer {}'.format(self.access_token)})
                self.token_timestamp = datetime.now()
                token_expiry = datetime.fromtimestamp(result.get('expires'))
                _LOGGER.debug('Token expires %s', token_expiry)
                return True
        except OSError as e:
            _LOGGER.error('Failed to authorize: %s', e)

    def refresh_access_token(self):
        """Refresh api token"""
        try:
            response = self.get(
                TELLDUS_LOCAL_REFRESH_TOKEN_URL.format(host=self._host))
            response.raise_for_status()
            result = response.json()
            self.access_token = result.get('token')
            self.token_timestamp = datetime.now()
            token_expiry = datetime.fromtimestamp(result.get('expires'))
            _LOGGER.debug('Token expires %s', token_expiry)
            return True
        except OSError as e:
            _LOGGER.error('Failed to refresh access token: %s', e)

    def authorized(self):
        """Return true if successfully authorized."""
        return self.access_token

    def maybe_refresh_token(self):
        """Refresh access_token if expired."""
        if self.token_timestamp:
            age = datetime.now() - self.token_timestamp
            if age > timedelta(seconds=(12 * 60 * 60)):  # 12 hours
                self.refresh_access_token()


class LiveAPISession(OAuth1Session):
    """Connection to the cloud service."""

    # pylint: disable=too-many-arguments
    def __init__(self,
                 public_key,
                 private_key,
                 token=None,
                 token_secret=None,
                 application=None):
        super().__init__(public_key, private_key, token, token_secret)
        self.url = TELLDUS_LIVE_API_URL
        self.access_token = None
        self.access_token_secret = None
        if application:
            self.headers.update({'X-Application': application})

    @property
    def authorize_url(self):
        """Retrieve URL for authorization."""
        _LOGGER.debug('Fetching request token')
        try:
            self.fetch_request_token(
                TELLDUS_LIVE_REQUEST_TOKEN_URL, timeout=TIMEOUT.seconds)
            _LOGGER.debug('Got request token')
            return self.authorization_url(TELLDUS_LIVE_AUTHORIZE_URL)
        except (OSError, ValueError) as e:
            _LOGGER.error('Failed to retrieve authorization URL: %s', e)

    def authorize(self):
        """Perform authorization."""
        try:
            _LOGGER.debug('Fetching access token')
            token = self._fetch_token(
                TELLDUS_LIVE_ACCESS_TOKEN_URL, timeout=TIMEOUT.seconds)
            _LOGGER.debug('Got access token')
            self.access_token = token['oauth_token']
            self.access_token_secret = token['oauth_token_secret']
            _LOGGER.debug('Authorized: %s', self.authorized)
            return self.authorized
        except (OSError, ValueError) as e:
            _LOGGER.error('Failed to authorize: %s', e)

    def maybe_refresh_token(self):
        """Refresh access_token if expired."""
        pass


class LocalUDPSession():
    TELLSTICK_SUCCESS = 0
    TELLSTICK_ERROR_DEVICE_NOT_FOUND = -3
    TELLSTICK_ERROR_UNKNOWN = -99

    def __init__(self, devicemanager, logger=None):
        self._request = None
        self._LOGGER = logger or logging.getLogger(__name__)
        self.devicemanager = devicemanager
        self.response = None
        self._exception = None
        self.status_code = None
        self.headers = {}
        self._json = None
        self.url = 'http://tellstick/'
        self.authorized = True

    def __str__(self):
        return self.response

    def devices(self, *args, params=None, timeout=None):
        """ creates device json """
        if args[0] == "list":
            devices = {"device": self.devicemanager.listdevices()}
            self._LOGGER.debug("Devices: %s: ", devices)
            self._json = devices
            return self.TELLSTICK_SUCCESS
        else:
            self._json = {"error": "Internal server error"}
            raise AttributeError

    def sensors(self, *args, params=None, timeout=None):
        """ creates sensor json """
        if args[0] == "list":
            sensor = {"sensor": self.devicemanager.listsensors()}
            self._LOGGER.debug("Sensor: %s: ", sensor)
            self._json = sensor
            self._LOGGER.debug("_json: %s: ", self._json)
            return self.TELLSTICK_SUCCESS
        else:
            self._json = {"error": "Internal server error"}
            raise AttributeError

    def sensor(self, *args, params=None, timeout=None):
        self._LOGGER.debug("Sensor id: %s ", params['id'])
        s = self.devicemanager.sensor(params['id'])
        self._LOGGER.debug("Got sensor: %s ", s)
        if s.isSensor():
            if args[0] == "info":
                self._json = s.deviceInfo()
                return self.TELLSTICK_SUCCESS
            else:
                self._json = {"error": "Internal server error"}
                raise AttributeError
        else:
            self._json = {"error": "Internal server error"}
            raise AttributeError

    def device(self, *args, params=None, timeout=None):
        """ runnns command on device """
        self._LOGGER.debug("Device id: %s ", params['id'])
        d = self.devicemanager.device(params['id'])
        self._LOGGER.debug("Got device: %s ", d)
        if d.isDevice():
            if args[0] == "turnOn":
                d.command(TURNON)
                self._json = {"status": "success"}
                return self.TELLSTICK_SUCCESS

            elif args[0] == "turnOff":
                d.command(TURNOFF)
                self._json = {"status": "success"}
                return self.TELLSTICK_SUCCESS
            elif args[0] == "info":
                self._json = d.deviceInfo()
                return self.TELLSTICK_SUCCESS
            else:
                self._json = {"error": "Internal server error"}
                raise AttributeError
        else:
            self._json = {"error": "Internal server error"}
            raise AttributeError

    def get(self, url, params=None, timeout=None):
        """ request get faker """
        self._LOGGER.debug("url: %s: ", url)
        self._request = url[len(self.url):].split('/')
        self._LOGGER.debug("get() _request[0]: %s: ", self._request[0])
        self._LOGGER.debug("get() _request[1:]: %s: ", self._request[1:])
        self._LOGGER.debug("get() params: %s: ", params)
        self.headers['content-type'] = "application/json; charset=utf-8"
        try:
            self.response = getattr(self,
                                    "%s" % self._request[0]
                                    )(*self._request[1:],
                                      params=params,
                                      timeout=timeout)
        except AttributeError:
            self._exception = "500 Internal server error %s" % self._request
            self.status_code = 500
            self._json = {"error": "Internal server error"}
            return self
        self.status_code = 200
        self._LOGGER.debug("get() _json: %s: ", self._json)
        return self

    @staticmethod
    def raise_for_status():
        """ pass status exception """
        pass

    def json(self):
        """ returns json """
        return self._json

    @staticmethod
    def maybe_refresh_token():
        """Refresh access_token if expired."""
        pass


class DefaultCallbackDispatcher(object):
    def __init__(self):
        super(DefaultCallbackDispatcher, self).__init__()

    def on_callback(self, callback, *args):
        callback(*args)


class AsyncioCallbackDispatcher(object):
    """Dispatcher for use with the event loop available in Python 3.4+.
    Callbacks will be dispatched on the thread running the event loop. The loop
    argument should be a BaseEventLoop instance, e.g. the one returned from
    asyncio.get_event_loop().
    """
    def __init__(self, loop):
        super(AsyncioCallbackDispatcher, self).__init__()
        self._loop = loop
        _LOGGER.debug("AsyncioCallbackDispatcher enabled with loop: %s", loop)

    def on_callback(self, callback, *args):
        _LOGGER.debug("AsyncioCallbackDispatcher called")
        self._loop.call_soon_threadsafe(callback, *args)


class Session:
    """Tellduslive session."""

    # pylint: disable=too-many-arguments
    def __init__(self,
                 public_key=None,
                 private_key=None,
                 token=None,
                 token_secret=None,
                 host=None,
                 application=None,
                 listen=False,  # listen for local UDP broadcasts
                 callback=None,  # callback for asynchrounous sensor updates
                 config=None,  # config for localUDPSession and async_listner
                 callback_dispatcher=None):

        if callback_dispatcher:
            self._callback_dispatcher = callback_dispatcher
        else:
            self._callback_dispatcher = DefaultCallbackDispatcher()

        _LOGGER.info('%s version %s', __name__, __version__)
        if not(all([public_key,
                    private_key,
                    token,
                    token_secret]) or
               all([listen]) or
               all([host, token]) or
               all([host, listen])):
            raise ValueError('Missing configuration')

        self._state = {}
        self._lock = RLock()
        if listen:
            from tellsticknet import devicemanager
            self._devicemanager = devicemanager.Tellstick(host=host,
                                                          logger=_LOGGER,
                                                          config=listen)

        host = host or (listen
                        if isinstance(listen, str)
                        else None)

        self._session = (
            LocalAPISession(host,
                            application,
                            token) if host and token and not public_key else
            LiveAPISession(public_key,
                           private_key,
                           token,
                           token_secret,
                           application) if (public_key and
                                            private_key and
                                            token and
                                            token_secret) else
            LocalUDPSession(self._devicemanager))

        if listen:
            _LOGGER.debug("Callback functions is: %s", callback)
            self.update()
            for d in self.devices:
                if not d.is_sensor:
                    self._devicemanager.adddevice({'name': d.name,
                                                   'id': d.device_id,
                                                   'parameters': d.parameters,
                                                   'protocol': d.protocol,
                                                   'model': d.model,
                                                   'client_id': d.client_id})
            self._setup_async_listener(self._devicemanager, callback)

    def _setup_async_listener(self, devicemanager, callback):
        """Starts listening for asynchronous UDP packets on the
        local network. If host is None, autodiscovery will be used."""
        def got(device):
            """Callback when ascynhronous packet is received.
            N.B. will be called in another thread."""
            local_id = ()
            with self._lock:
                callbackdevice = None
                """ check i device is a sensor """
                if 'sensorId' in device:
                    local_id = (device['protocol'],
                                device['model'],
                                str(device['sensorId']))
                    _LOGGER.debug('Received asynchronous packet %s:%s:%s',
                                  *local_id)
                    _LOGGER.debug('Received asynchronous data %s from %s',
                                  device['data'], local_id)

                    sensor = next((sensor
                                   for sensor in self.sensors
                                   if ((sensor.protocol,
                                        sensor.model,
                                        str(sensor.sensorId)) == local_id)),
                                  None)

                    if not sensor:
                        _LOGGER.info('Found no corresponding device on server'
                                     'for packet %s:%s:%s %s', *local_id,
                                     'new sensor added')
                        self._state.update({'_' + str(device['id']): device})

                        sensor = next((sensor
                                       for sensor in self.sensors
                                       if ((sensor.protocol,
                                            sensor.model,
                                            str(sensor.sensorId)
                                            ) == local_id)),
                                      None)

                    _LOGGER.debug('Got asynchronous update from sensor %s',
                                  sensor.name)

                    sensor.device.update({"data": device['data']})
                    callbackdevice = sensor.device
                else:
                    for param in device.get('parameters'):
                        if param.get('name') == 'unit':
                            unit = param.get('value')
                        elif param.get('name') == 'house':
                            house = param.get('value')
                    local_id = (house, unit)
                    _LOGGER.debug('Received asynchronous data %s from %s',
                                  device, local_id)
                    for dev in self.devices:
                        if dev.parameters:
                            for param in dev.parameters:
                                if param.get('name') == 'unit':
                                    unit = param.get('value')
                                elif param.get('name') == 'house':
                                    house = param.get('value')
                            device_id = (house, unit)
                            if device_id == local_id:
                                _LOGGER.debug('Found device %s from %s',
                                              dev.device, device_id)
                                break
                    if not dev:
                        _LOGGER.info('Found no corresponding device on server'
                                     'for packet %s %s', local_id,
                                     'new device  added')
                        self._state.update({str(device['id']): device})

                        dev = next((dev
                                    for dev in self.devices
                                    if (dev.device_id == device['id'])), None)

                    _LOGGER.debug('Got asynchronous update from device %s',
                                  dev.name)
                    dev.device.update({'state': device.get('state')})
                    callbackdevice = dev.device
                _LOGGER.debug("callback device id %s",
                              callbackdevice.get('id'))
                self._callback_dispatcher.on_callback(callback,
                                                      callbackdevice)

        _LOGGER.info('Starting asynchronous listener thread')
        devicemanager.async_listen(callback=got)

    @property
    def authorize_url(self):
        """Retrieve URL for authorization."""
        return self._session.authorize_url

    def authorize(self):
        """Perform authorization."""
        return self._session.authorize()

    @property
    def access_token(self):
        """Return access token."""
        return self._session.access_token

    @property
    def is_authorized(self):
        """Return true if successfully authorized."""
        return self._session.authorized

    @property
    def access_token_secret(self):
        """Return the token secret."""
        return self._session.access_token_secret

    def _device(self, device_id):
        """Return the raw representaion of a device."""
        with self._lock:
            return self._state.get(device_id)

    def _request(self, path, **params):
        """Send a request to the Tellstick Live API."""
        try:
            self._session.maybe_refresh_token()
            url = urljoin(self._session.url, path)
            _LOGGER.debug('Request %s %s', url, params)
            response = self._session.get(url,
                                         params=params,
                                         timeout=TIMEOUT.seconds)
            response.raise_for_status()
            _LOGGER.debug('Response %s %s %s',
                          response.status_code,
                          response.headers['content-type'],
                          response.json())
            response = response.json()
            if 'error' in response:
                raise OSError(response['error'])
            return response
        except OSError as error:
            _LOGGER.warning('Failed request: %s', error)

    def execute(self, method, **params):
        """Make request, check result if successful."""
        with self._lock:
            response = self._request(method, **params)
            return response and response.get('status') == 'success'

    def _request_devices(self):
        """Request list of devices from server."""
        res = self._request('devices/list',
                            supportedMethods=SUPPORTED_METHODS,
                            includeIgnored=0)
        return res.get('device') if res else None

    def _request_device(self, id):
        """Request list of devices from server."""
        res = self._request('device/info',
                            id=id)
        return res if res else None

    def _request_sensor(self, id):
        """Request list of devices from server."""
        res = self._request('sensor/info',
                            id=id)
        return res if res else None

    def _request_sensors(self):
        """Request list of sensors from server."""
        res = self._request('sensors/list',
                            includeValues=1,
                            includeScale=1,
                            includeIgnored=0)
        return res.get('sensor') if res else None

    def update(self):
        """Updates all devices and sensors from server."""
        with self._lock:
            def collect(devices, is_sensor=False):
                """Update local state.
                N.B. We prefix sensors with '_',
                since apparently sensors and devices
                do not share name space and there can
                be collissions.
                FIXME: Remove this hack."""
                self._state.update({'_' * is_sensor + str(device['id']): device
                                    for device in devices or {}
                                    if device['name'] and
                                    not (is_sensor and
                                   'data' not in device)})

            devices = self._request_devices()
            for i, d in enumerate(devices):
                if d.get('id') in list(self.device_ids):
                    _LOGGER.debug("already known device")
                    req_dev = self.device(d.get('id'))
                    d.update({'parameters': req_dev.parameters})
                    d.update({'protocol': req_dev.protocol})
                    d.update({'model': req_dev.model})
                    d.update({'client_id': req_dev.client_id})
                    devices[i].update(d)
                else:
                    _LOGGER.debug("Getting protocol and parameters",
                                  "for new device")
                    req_dev = self._request_device(d.get('id'))
                    d.update({'parameters': req_dev.get('parameter')})
                    d.update({'protocol': req_dev.get('protocol')})
                    d.update({'model': req_dev.get('model')})
                    d.update({'client_id': req_dev.get('client')})
                    devices[i].update(d)
            collect(devices)

            sensors = self._request_sensors()
            collect(sensors, True)

            return (devices is not None and
                    sensors is not None)

    def device(self, device_id):
        """Return a device object."""
        return Device(self, device_id)

    @property
    def sensors(self):
        """Return only sensors.
        FIXME: terminology device vs device."""
        return (device
                for device in self.devices
                if device.is_sensor)

    @property
    def devices(self):
        """Request representations of all devices."""
        return (self.device(device_id) for device_id in self.device_ids)

    @property
    def device_ids(self):
        """List of known device ids."""
        with self._lock:
            return self._state.keys()


class Device:
    """Tellduslive device."""

    def __init__(self, session, device_id):
        self._session = session
        self._device_id = device_id

    def __str__(self):
        if self.is_sensor:
            items = ', '.join(str(item) for item in self.items)
            return 'Sensor #{id:>9} {name:<20} ({items})'.format(
                id=self.device_id,
                name=self.name or UNNAMED_DEVICE,
                items=items)
        else:
            return ('Device #{id:>9} {name:<20} '
                    '({state}:{value}) [{methods}]').format(
                        id=self.device_id,
                        name=self.name or UNNAMED_DEVICE,
                        state=self._str_methods(self.state),
                        value=self.statevalue,
                        methods=self._str_methods(self.methods))

    def __getattr__(self, name):
        if (self.device and
            name in ['name', 'state', 'battery', 'unit', 'house',
                     'model', 'protocol', 'parameters', 'client_id',
                     'lastUpdated', 'methods', 'data', 'sensorId']):
            return self.device.get(name)

    @property
    def device(self):
        """Return the raw representation of the device."""
        # pylint: disable=protected-access
        return self._session._device(self.device_id)

    @property
    def device_id(self):
        """Id of device."""
        return self._device_id

    @staticmethod
    def _str_methods(val):
        """String representation of methods or state."""
        res = []
        for method in METHODS:
            if val & method:
                res.append(METHODS[method].upper())
        return "|".join(res)

    def _execute(self, command, **params):
        """Send command to server and update local state."""
        params.update(id=self.device_id)
        # Corresponding API methods
        method = 'device/{}'.format(METHODS[command])
        if self._session.execute(method, **params):
            self.device['state'] = command
            return True

    @property
    def is_sensor(self):
        """Return true if this is a sensor."""
        return 'data' in self.device

    @property
    def statevalue(self):
        """State value of device."""
        val = self.device.get('statevalue')
        return val if val and val != 'unde' else 0

    @property
    def is_on(self):
        """Return true if device is on."""
        return (self.state == TURNON or
                self.state == DIM)

    @property
    def is_down(self):
        """Return true if device is down."""
        return self.state == DOWN

    @property
    def dim_level(self):
        """Return current dim level."""
        try:
            return int(self.statevalue)
        except (TypeError, ValueError):
            return None

    def turn_on(self):
        """Turn device on."""
        return self._execute(TURNON)

    def turn_off(self):
        """Turn device off."""
        return self._execute(TURNOFF)

    def dim(self, level):
        """Dim device."""
        if self._execute(DIM, level=level):
            self.device['statevalue'] = level
            return True

    def up(self):
        """Pull device up."""
        return self._execute(UP)

    def down(self):
        """Pull device down."""
        return self._execute(DOWN)

    def stop(self):
        """Stop device."""
        return self._execute(STOP)

    @property
    def items(self):
        """Return sensor items for sensor."""
        return (SensorItem(item) for item in self.data) if self.data else []

    def item(self, name, scale):
        """Return sensor item."""
        return next((item for item in self.items
                     if (item.name == name and
                         int(item.scale) == int(scale))), None)

    def value(self, name, scale):
        """Return value of sensor item."""
        return self.item(name, scale).value


class SensorItem:
    # pylint: disable=too-few-public-methods, no-member
    """Reference to a sensor data item."""
    def __init__(self, data):
        vars(self).update(data)

    def __str__(self):
        return '{name}={value}'.format(
            name=self.name, value=self.value)


def read_credentials():
    from sys import argv
    from os.path import join, dirname, expanduser
    for directory in [
            dirname(argv[0]),
            expanduser('~')]:
        try:
            with open(join(directory, '.tellduslive.conf')) as config:
                return dict(
                    x.split(': ')
                    for x in config.read().strip().splitlines()
                    if not x.startswith('#')
                    if not x.startswith('local'))
        except OSError:
            continue
    return {}


if __name__ == '__main__':
    """Dump configured devices and sensors."""
    logging.basicConfig(level=logging.INFO)
    credentials = read_credentials()
    session = Session(**credentials)
    session.update()
    print('Devices\n'
          '-------')
    for device in session.devices:
        print(device)
        for item in device.items:
            print('- {}'.format(item))
