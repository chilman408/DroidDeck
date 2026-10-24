// SPDX-License-Identifier: GPL-3.0-only
import java.lang.reflect.Proxy;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Disposable delivery probe, not a GNSS HAL or production location service. */
public final class TeslaLocationProbe {
    public static void main(String[] args) {
        try {
            if (args.length != 2 || !args[1].matches("teslaandroid_probe_[a-f0-9]{16}")) {
                throw new IllegalArgumentException("Unsupported probe provider");
            }
            int uid = (Integer) Class.forName("android.os.Process").getMethod("myUid").invoke(null);
            if (args[0].equals("--observe") && uid == 1000) observe(args[1]);
            else if (args[0].equals("--create") && uid == 0) System.out.println(create(args[1]) ? "OK" : "EXISTS");
            else if (args[0].equals("--inject") && uid == 0) { inject(args[1]); System.out.println("OK"); }
            else if (args[0].equals("--absent") && uid == 0) System.out.println(present(args[1]) ? "PRESENT" : "OK");
            else throw new IllegalArgumentException("Unsupported probe identity or operation");
            System.exit(0);
        } catch (Throwable error) {
            // No fix values or Binder error messages are included in diagnostics.
            System.err.println("Location probe operation failed: " + error.getClass().getSimpleName());
            System.exit(1);
        }
    }

    private static Object service() throws Exception {
        Class<?> binder = Class.forName("android.os.IBinder");
        Object handle = Class.forName("android.os.ServiceManager").getMethod("getService", String.class)
            .invoke(null, "location");
        if (handle == null) throw new IllegalStateException("Location service unavailable");
        return Class.forName("android.location.ILocationManager$Stub").getMethod("asInterface", binder).invoke(null, handle);
    }

    private static boolean present(String name) throws Exception {
        return ((List<?>) Class.forName("android.location.ILocationManager").getMethod("getAllProviders")
            .invoke(service())).contains(name);
    }

    private static boolean create(String name) throws Exception {
        Object service = service();
        Class<?> type = Class.forName("android.location.ILocationManager");
        if (((List<?>) type.getMethod("getAllProviders").invoke(service)).contains(name)) {
            return false;
        }
        Class<?> properties = Class.forName("android.location.provider.ProviderProperties");
        Class<?> builderClass = Class.forName("android.location.provider.ProviderProperties$Builder");
        Object builder = builderClass.getConstructor().newInstance();
        builderClass.getMethod("setHasSpeedSupport", boolean.class).invoke(builder, true);
        builderClass.getMethod("setHasBearingSupport", boolean.class).invoke(builder, true);
        builderClass.getMethod("setPowerUsage", int.class).invoke(builder, 1);
        builderClass.getMethod("setAccuracy", int.class).invoke(builder, 1);
        type.getMethod("addTestProvider", String.class, properties, List.class, String.class, String.class)
            .invoke(service, name, builderClass.getMethod("build").invoke(builder), Collections.emptyList(), "android", null);
        if (!((List<?>) type.getMethod("getAllProviders").invoke(service)).contains(name)) {
            throw new IllegalStateException("Provider rejected");
        }
        type.getMethod("setTestProviderEnabled", String.class, boolean.class, String.class, String.class)
            .invoke(service, name, true, "android", null);
        return true;
    }

    private static void inject(String name) throws Exception {
        Class<?> locationClass = Class.forName("android.location.Location");
        Object location = locationClass.getConstructor(String.class).newInstance(name);
        locationClass.getMethod("setLatitude", double.class).invoke(location, 0.0);
        locationClass.getMethod("setLongitude", double.class).invoke(location, 0.0);
        locationClass.getMethod("setAccuracy", float.class).invoke(location, 5f);
        locationClass.getMethod("setSpeed", float.class).invoke(location, 2f);
        locationClass.getMethod("setBearing", float.class).invoke(location, 90f);
        locationClass.getMethod("setTime", long.class).invoke(location, System.currentTimeMillis());
        long elapsed = (Long) Class.forName("android.os.SystemClock").getMethod("elapsedRealtimeNanos").invoke(null);
        locationClass.getMethod("setElapsedRealtimeNanos", long.class).invoke(location, elapsed);
        Class.forName("android.location.ILocationManager")
            .getMethod("setTestProviderLocation", String.class, locationClass, String.class, String.class)
            .invoke(service(), name, location, "android", null);
    }

    private static void observe(String name) throws Exception {
        Class.forName("android.os.Looper").getMethod("prepareMainLooper").invoke(null);
        Class<?> activityThread = Class.forName("android.app.ActivityThread");
        Object context = activityThread.getMethod("getSystemContext")
            .invoke(activityThread.getMethod("systemMain").invoke(null));
        Object manager = Class.forName("android.content.Context").getMethod("getSystemService", String.class)
            .invoke(context, "location");
        Class<?> managerClass = Class.forName("android.location.LocationManager");
        Class<?> listenerClass = Class.forName("android.location.LocationListener");
        final CountDownLatch received = new CountDownLatch(1);
        final AtomicReference<Object> result = new AtomicReference<>();
        Object listener = Proxy.newProxyInstance(listenerClass.getClassLoader(), new Class<?>[] { listenerClass },
            (proxy, method, arguments) -> {
                if (method.getName().equals("onLocationChanged") && arguments != null && arguments.length > 0) {
                    Object location = arguments[0];
                    if (location instanceof List) {
                        List<?> list = (List<?>) location;
                        location = list.isEmpty() ? null : list.get(list.size() - 1);
                    }
                    if (location != null) { result.set(location); received.countDown(); }
                }
                if (method.getName().equals("hashCode")) return System.identityHashCode(proxy);
                if (method.getName().equals("equals")) return proxy == arguments[0];
                if (method.getName().equals("toString")) return "TeslaLocationProbeListener";
                return null;
            });
        try {
            Executor executor = command -> command.run();
            managerClass.getMethod("requestLocationUpdates", String.class, long.class, float.class,
                Executor.class, listenerClass).invoke(manager, name, 0L, 0f, executor, listener);
            if (!received.await(8, TimeUnit.SECONDS)) throw new IllegalStateException("Delivery timed out");
            Class<?> type = Class.forName("android.location.Location");
            Object delivered = result.get();
            if ((Double) type.getMethod("getLatitude").invoke(delivered) != 0.0 ||
                (Double) type.getMethod("getLongitude").invoke(delivered) != 0.0 ||
                (Float) type.getMethod("getAccuracy").invoke(delivered) != 5f ||
                !(Boolean) type.getMethod("hasSpeed").invoke(delivered) ||
                !(Boolean) type.getMethod("hasBearing").invoke(delivered) ||
                (Float) type.getMethod("getSpeed").invoke(delivered) != 2f ||
                (Float) type.getMethod("getBearing").invoke(delivered) != 90f ||
                !(Boolean) type.getMethod("isMock").invoke(delivered)) throw new IllegalStateException("Delivery mismatch");
            System.out.println("{\"version\":1,\"delivered\":true,\"motion\":true,\"mock\":true}");
        } finally {
            managerClass.getMethod("removeUpdates", listenerClass).invoke(manager, listener);
        }
    }
}
