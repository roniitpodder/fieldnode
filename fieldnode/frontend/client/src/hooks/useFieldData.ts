import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  zoneSocketUrl,
  type CropPreset,
  type Device,
  type Farm,
  type Notification,
  type Schedule,
  type SensorReading,
  type WateringEvent,
  type Zone,
  type ZoneOverview,
} from "@/lib/api";

const OVERVIEW_POLL_MS = 10000;

// ESP32 posts telemetry every 30 seconds.
// Allow a few missed/delayed posts before declaring it stale.
const STALE_THRESHOLD_MS = 90000;

export type FieldData = ReturnType<typeof useFieldData>;

/**
 * Single source of truth for the dashboard. Farms/zones/crops load once;
 * per-zone telemetry reloads whenever the selected zone changes, is polled as a
 * safety net, and is pushed live over the zone WebSocket.
 */
export function useFieldData(enabled: boolean) {
  const [farms, setFarms] = useState<Farm[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [crops, setCrops] = useState<CropPreset[]>([]);
  const [zoneId, setZoneId] = useState<string | null>(null);

  const [overview, setOverview] = useState<ZoneOverview | null>(null);
  const [readings, setReadings] = useState<SensorReading[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [events, setEvents] = useState<WateringEvent[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [live, setLive] = useState(false);
  const [hardwareOnline, setHardwareOnline] = useState<boolean>(true);

  const zoneIdRef = useRef<string | null>(null);
  zoneIdRef.current = zoneId;

  /* ---------------------------------------------------------- base data */
  const loadBase = useCallback(async () => {
    setError(null);
    try {
      const [farmList, cropList] = await Promise.all([api.listFarms(), api.listCrops()]);
      setFarms(farmList);
      setCrops(cropList);

      const zoneList = await api.listZones(farmList[0]?.id);
      setZones(zoneList);
      setZoneId((current) => {
        if (current && zoneList.some((zone) => zone.id === current)) return current;
        return zoneList[0]?.id ?? null;
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your farm.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    setLoading(true);
    loadBase();
  }, [enabled, loadBase]);

  /* ------------------------------------------------------ per-zone data */
  const loadZone = useCallback(async (id: string) => {
    try {
      const [zoneOverview, zoneReadings, zoneDevices, zoneEvents, zoneSchedules] = await Promise.all([
        api.zoneOverview(id),
        api.zoneReadings(id, 24).catch(() => [] as SensorReading[]),
        api.listDevices(id).catch(() => [] as Device[]),
        api.pumpEvents(id, 50).catch(() => [] as WateringEvent[]),
        api.listSchedules(id).catch(() => [] as Schedule[]),
      ]);
      if (zoneIdRef.current !== id) return; // a newer zone was selected mid-flight
      
      setOverview(zoneOverview);
      setReadings(zoneReadings);
      setDevices(zoneDevices);
      setEvents(zoneEvents);
      setSchedules(zoneSchedules);
      setLastSync(new Date());

      // Check if device reports online status
      const primaryDevice = zoneDevices[0];
      if (primaryDevice && typeof (primaryDevice as any).hardware_online === "boolean") {
        setHardwareOnline((primaryDevice as any).hardware_online);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load zone telemetry.");
    }
  }, []);

  useEffect(() => {
    if (!enabled || !zoneId) return;
    loadZone(zoneId);
    const timer = window.setInterval(() => loadZone(zoneId), OVERVIEW_POLL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, zoneId, loadZone]);

  /* --------------------------------------------------------- activity */
  const loadActivity = useCallback(async () => {
    try {
      const [feed, notes] = await Promise.all([
        api.activity(undefined, 100),
        api.notifications().catch(() => [] as Notification[]),
      ]);
      setNotifications(notes);
      return feed;
    } catch {
      return [] as WateringEvent[];
    }
  }, []);

  const [activity, setActivity] = useState<WateringEvent[]>([]);
  useEffect(() => {
    if (!enabled) return;
    loadActivity().then(setActivity);
    const timer = window.setInterval(() => loadActivity().then(setActivity), OVERVIEW_POLL_MS * 2);
    return () => window.clearInterval(timer);
  }, [enabled, loadActivity]);

  /* -------------------------------------------- live telemetry over WS */
  useEffect(() => {
    if (!enabled || !zoneId) return;
    const url = zoneSocketUrl(zoneId);
    if (!url) return;

    let socket: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;

    const connect = () => {
      if (closed) return;
      try {
        socket = new WebSocket(url);
      } catch {
        return;
      }

      socket.onopen = () => setLive(true);

      socket.onmessage = (message) => {
        let payload: any;
        try {
          payload = JSON.parse(message.data);
        } catch {
          return;
        }

        if (payload.event === "reading") {
          setLastSync(new Date());
          setHardwareOnline(true); // Fresh reading means hardware is active
          setOverview((current) =>
            current
              ? {
                  ...current,
                  soil_moisture:
                    payload.soil_moisture ?? current.soil_moisture,
                  temperature:
                    payload.temperature ?? current.temperature,
                  sunlight_pct:
                    payload.sunlight_pct ?? current.sunlight_pct,
                  raining_now:
                    payload.rain_detected ?? current.raining_now,
                }
              : current,
          );
          setReadings((current) => {
            const next = [
              ...current,
              {
                id: `ws-${payload.timestamp ?? Date.now()}`,
                device_id: payload.device_id,
                timestamp: payload.timestamp ?? new Date().toISOString(),
                soil_moisture: payload.soil_moisture ?? null,
                temperature: payload.temperature ?? null,
                humidity: payload.humidity ?? null,
                light_level: payload.light_level ?? null,
                sunlight_pct: payload.sunlight_pct ?? null,
                rain_detected: payload.rain_detected ?? null,
                rain_intensity: payload.rain_intensity ?? null,
                sensor_fault: false,
              } as SensorReading,
            ];
            return next.slice(-300);
          });
        }

        if (payload.event === "pump_state" || payload.event === "pump_command_queued") {
          setLastSync(new Date());
          setHardwareOnline(true);
          if (typeof payload.pump_running === "boolean") {
            setOverview((current) => (current ? { ...current, pump_running: payload.pump_running } : current));
          }
          if (zoneIdRef.current) loadZone(zoneIdRef.current);
        }
      };

      socket.onclose = () => {
        setLive(false);
        if (!closed) retry = window.setTimeout(connect, 5000);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      closed = true;
      setLive(false);
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, [enabled, zoneId, loadZone]);

  // Compute staleness based on lastSync time difference
  const isStale = lastSync ? Date.now() - lastSync.getTime() > STALE_THRESHOLD_MS : true;
  const isHardwareOffline = !hardwareOnline || isStale;

  const refresh = useCallback(async () => {
    await loadBase();
    if (zoneIdRef.current) await loadZone(zoneIdRef.current);
    setActivity(await loadActivity());
  }, [loadBase, loadZone, loadActivity]);

  const refreshZone = useCallback(async () => {
    if (zoneIdRef.current) await loadZone(zoneIdRef.current);
    setActivity(await loadActivity());
  }, [loadZone, loadActivity]);

  return {
    farms,
    zones,
    crops,
    zoneId,
    setZoneId,
    overview,
    readings,
    devices,
    events,
    schedules,
    notifications,
    activity,
    loading,
    error,
    lastSync,
    live,
    isHardwareOffline, // <--- Exposed for UI warning and state overriding
    refresh,
    refreshZone,
    setZones,
    setNotifications,
  };
}