import { API } from '../config/api';

let _sensorWS = null;

export function connectSensorWS(assetId, onData) {
  if (_sensorWS) { try { _sensorWS.close(); } catch (e) {} }
  const wsBase = API.replace('http', 'ws');
  _sensorWS = new WebSocket(`${wsBase}/ws/sensors/${encodeURIComponent(assetId)}`);
  _sensorWS.onmessage = (ev) => {
    try { onData(JSON.parse(ev.data)); } catch (e) {}
  };
  _sensorWS.onerror = () => {};
}
