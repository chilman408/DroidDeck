// SPDX-License-Identifier: GPL-3.0-only
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.List;

/** Root-only, lease-tagged Android mock-location adapter. It never hides the mock flag. */
public final class TeslaLocationBridge {
    private static final String PROVIDER = "gps", PACKAGE = "android";
    private static Object service;
    private static Class<?> serviceClass;

    public static void main(String[] args) {
        try {
            if (args.length != 2 || !args[1].matches("[a-f0-9]{32}")) {
                throw new IllegalArgumentException("Invalid location operation or identity");
            }
            int uid = (Integer) Class.forName("android.os.Process").getMethod("myUid").invoke(null);
            if ((args[0].equals("--verify") || args[0].equals("--verify-no-motion")) && uid == 1000) {
                verify(args[0].equals("--verify"));
                System.out.println("{\"version\":1,\"verified\":true,\"mock\":true}"); System.exit(0); return;
            }
            if (uid != 0) throw new IllegalArgumentException("Invalid location identity");
            String tag = "teslaandroid_gps:" + args[1];
            Class<?> binder = Class.forName("android.os.IBinder");
            Object handle = Class.forName("android.os.ServiceManager").getMethod("getService", String.class).invoke(null, "location");
            if (handle == null) throw new IllegalStateException("Location service unavailable");
            serviceClass = Class.forName("android.location.ILocationManager");
            service = Class.forName("android.location.ILocationManager$Stub").getMethod("asInterface", binder).invoke(null, handle);
            if (args[0].equals("--open")) open(tag);
            else if (args[0].equals("--apply")) apply(tag);
            else if (args[0].equals("--close")) close(tag);
            else if (!args[0].equals("--inspect")) throw new IllegalArgumentException("Invalid location operation");
            boolean present = present(), owned = present && owned(tag);
            System.out.println("{\"version\":1,\"present\":" + present + ",\"owned\":" + owned + "}");
            System.exit(0);
        } catch (Throwable error) {
            System.err.println("Location operation failed: " + error.getClass().getSimpleName());
            System.exit(1);
        }
    }

    private static boolean present() throws Exception {
        return ((List<?>) serviceClass.getMethod("getAllProviders").invoke(service)).contains(PROVIDER);
    }

    private static boolean owned(String tag) throws Exception {
        return (Boolean) serviceClass.getMethod("isProviderPackage", String.class, String.class, String.class)
            .invoke(service, PROVIDER, PACKAGE, tag);
    }

    private static void requireOwned(String tag) throws Exception {
        if (!present() || !owned(tag)) throw new IllegalStateException("Location provider conflict");
    }

    private static void open(String tag) throws Exception {
        if (present()) { requireOwned(tag); return; }
        Class<?> builderClass = Class.forName("android.location.provider.ProviderProperties$Builder");
        Object builder = builderClass.getConstructor().newInstance();
        builderClass.getMethod("setHasSpeedSupport", boolean.class).invoke(builder, true);
        builderClass.getMethod("setHasBearingSupport", boolean.class).invoke(builder, true);
        builderClass.getMethod("setPowerUsage", int.class).invoke(builder, 1);
        builderClass.getMethod("setAccuracy", int.class).invoke(builder, 1);
        Class<?> properties = Class.forName("android.location.provider.ProviderProperties");
        serviceClass.getMethod("addTestProvider", String.class, properties, List.class, String.class, String.class)
            .invoke(service, PROVIDER, builderClass.getMethod("build").invoke(builder), Collections.emptyList(), PACKAGE, tag);
        requireOwned(tag);
        serviceClass.getMethod("setTestProviderEnabled", String.class, boolean.class, String.class, String.class)
            .invoke(service, PROVIDER, true, PACKAGE, tag);
    }

    private static void close(String tag) throws Exception {
        if (!present()) return;
        requireOwned(tag);
        serviceClass.getMethod("removeTestProvider", String.class, String.class, String.class)
            .invoke(service, PROVIDER, PACKAGE, tag);
        if (present()) throw new IllegalStateException("Location cleanup unverified");
    }

    private static void apply(String tag) throws Exception {
        requireOwned(tag);
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        byte[] buffer = new byte[256];
        int count;
        while ((count = System.in.read(buffer)) != -1) {
            if (bytes.size() + count > 2048) throw new IllegalArgumentException("Location input too large");
            bytes.write(buffer, 0, count);
        }
        Class<?> jsonClass = Class.forName("org.json.JSONObject");
        Object json = jsonClass.getConstructor(String.class).newInstance(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        if ((Integer) jsonClass.getMethod("length").invoke(json) != 6) throw new IllegalArgumentException("Invalid location");
        for (String name : new String[] { "latitude", "longitude", "accuracy", "timestamp", "speed", "bearing" }) {
            if (!(Boolean) jsonClass.getMethod("has", String.class).invoke(json, name)) throw new IllegalArgumentException("Invalid location");
        }
        double latitude = number(jsonClass, json, "latitude"), longitude = number(jsonClass, json, "longitude");
        double accuracy = number(jsonClass, json, "accuracy"), timestamp = number(jsonClass, json, "timestamp");
        double age = System.currentTimeMillis() - timestamp;
        if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180 || (float) accuracy <= 0 || accuracy > 100000 ||
            timestamp <= 0 || timestamp > 9007199254740991.0 || timestamp != Math.floor(timestamp) || age > 30000 || age < -5000) {
            throw new IllegalArgumentException("Invalid location");
        }
        Double speed = optional(jsonClass, json, "speed"), bearing = optional(jsonClass, json, "bearing");
        if ((speed != null && (speed < 0 || speed > 150)) || (bearing != null && (bearing < 0 || bearing >= 360))) {
            throw new IllegalArgumentException("Invalid location");
        }
        Class<?> type = Class.forName("android.location.Location");
        Object location = type.getConstructor(String.class).newInstance(PROVIDER);
        type.getMethod("setLatitude", double.class).invoke(location, latitude);
        type.getMethod("setLongitude", double.class).invoke(location, longitude);
        type.getMethod("setAccuracy", float.class).invoke(location, (float) accuracy);
        type.getMethod("setTime", long.class).invoke(location, (long) timestamp);
        long elapsed = (Long) Class.forName("android.os.SystemClock").getMethod("elapsedRealtimeNanos").invoke(null);
        type.getMethod("setElapsedRealtimeNanos", long.class).invoke(location, Math.max(1L, elapsed - (long) Math.max(0, age) * 1000000L));
        if (speed != null) type.getMethod("setSpeed", float.class).invoke(location, speed.floatValue());
        if (bearing != null) type.getMethod("setBearing", float.class).invoke(location, bearing.floatValue());
        requireOwned(tag);
        serviceClass.getMethod("setTestProviderLocation", String.class, type, String.class, String.class)
            .invoke(service, PROVIDER, location, PACKAGE, tag);
    }

    private static double number(Class<?> type, Object json, String name) throws Exception {
        Object value = type.getMethod("get", String.class).invoke(json, name);
        if (!(value instanceof Number) || !Double.isFinite(((Number) value).doubleValue())) throw new IllegalArgumentException("Invalid location");
        return ((Number) value).doubleValue();
    }

    private static Double optional(Class<?> type, Object json, String name) throws Exception {
        return (Boolean) type.getMethod("isNull", String.class).invoke(json, name) ? null : number(type, json, name);
    }

    private static void verify(boolean motion) throws Exception {
        Class.forName("android.os.Looper").getMethod("prepareMainLooper").invoke(null);
        Class<?> thread = Class.forName("android.app.ActivityThread");
        Object context = thread.getMethod("getSystemContext").invoke(thread.getMethod("systemMain").invoke(null));
        Object manager = Class.forName("android.content.Context").getMethod("getSystemService", String.class).invoke(context, "location");
        Object value = Class.forName("android.location.LocationManager").getMethod("getLastKnownLocation", String.class).invoke(manager, PROVIDER);
        Class<?> type = Class.forName("android.location.Location");
        if (value == null || (Double) type.getMethod("getLatitude").invoke(value) != 0.0 ||
            (Double) type.getMethod("getLongitude").invoke(value) != 0.0 ||
            (Float) type.getMethod("getAccuracy").invoke(value) != 5f ||
            (Boolean) type.getMethod("hasSpeed").invoke(value) != motion ||
            (Boolean) type.getMethod("hasBearing").invoke(value) != motion ||
            (motion && ((Float) type.getMethod("getSpeed").invoke(value) != 2f ||
              (Float) type.getMethod("getBearing").invoke(value) != 90f)) ||
            !(Boolean) type.getMethod("isMock").invoke(value)) throw new IllegalStateException("Location delivery unverified");
    }
}
