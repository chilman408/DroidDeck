// SPDX-License-Identifier: GPL-3.0-only
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

public final class GpsBridgeReflectionTest {
    public static final class FakeJson {
        private final Object value;
        public FakeJson(Object input) { value = input; }
        public Object get(String name) { return value; }
        public boolean isNull(String name) { return value == null; }
    }
    public static void main(String[] args) throws Exception {
        Method number = TeslaLocationBridge.class.getDeclaredMethod("number", Class.class, Object.class, String.class);
        Method optional = TeslaLocationBridge.class.getDeclaredMethod("optional", Class.class, Object.class, String.class);
        number.setAccessible(true); optional.setAccessible(true);
        assert (Double) number.invoke(null, FakeJson.class, new FakeJson(12.5), "x") == 12.5;
        assert (Double) number.invoke(null, FakeJson.class, new FakeJson(0), "x") == 0.0;
        assert optional.invoke(null, FakeJson.class, new FakeJson(null), "x") == null;
        assert (Double) optional.invoke(null, FakeJson.class, new FakeJson(2.0), "x") == 2.0;
        for (Object value : new Object[] { "1", Boolean.TRUE, Double.NaN, Double.POSITIVE_INFINITY, null }) {
            try {
                number.invoke(null, FakeJson.class, new FakeJson(value), "x");
                throw new AssertionError("Invalid generated number accepted");
            } catch (InvocationTargetException error) {
                assert error.getCause() instanceof IllegalArgumentException;
            }
        }
        System.out.println("GPS numeric/null reflection tests passed; no Android operations");
    }
}
