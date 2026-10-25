// SPDX-License-Identifier: GPL-3.0-only
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/** Invalid matrices must fail before any Android stub/service is reached. */
public final class LogicalCaptureValidationTest {
    public static void main(String[] args) throws Exception {
        Method method = TeslaLogicalCapture.class.getDeclaredMethod("calibrate", String[].class);
        method.setAccessible(true);
        String[][] changes = {
            {"1", "0"}, {"1", "-0.1"}, {"1", "1.1"}, {"1", "NaN"}, {"1", "Infinity"}, {"1", "invalid"},
            {"2", "0.1"}, {"3", "-1"}, {"3", "1921"}, {"4", "0.1"},
            {"5", "0"}, {"5", "-1"}, {"5", "1.1"}, {"6", "-1"}, {"6", "1921"}
        };
        for (String[] change : changes) {
            String[] matrix = {"--calibrate", "1", "0", "0", "0", "1", "0"};
            matrix[Integer.parseInt(change[0])] = change[1];
            try {
                method.invoke(null, (Object) matrix);
                throw new AssertionError("Invalid matrix accepted");
            } catch (InvocationTargetException error) {
                if (!(error.getCause() instanceof IllegalArgumentException)) {
                    throw new AssertionError("Reached Android or unexpected failure", error.getCause());
                }
            }
        }
        System.out.println("Invalid calibration checks passed: " + changes.length);
    }
}
