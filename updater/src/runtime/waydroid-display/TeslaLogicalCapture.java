// SPDX-License-Identifier: GPL-3.0-only
import android.graphics.Bitmap;
import android.hardware.HardwareBuffer;
import android.os.IBinder;
import android.view.InputDevice;
import java.lang.reflect.Method;

/** Capture the logical WindowManager display, without restarting the compositor. */
public final class TeslaLogicalCapture {
    public static void main(String[] args) {
        try {
            if (args.length == 0) capture();
            else if (args.length == 7 && args[0].equals("--calibrate")) calibrate(args);
            else throw new IllegalArgumentException("Unsupported helper operation");
        } catch (Throwable error) {
            error.printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static void calibrate(String[] args) throws Exception {
        float[] matrix = new float[6];
        for (int i = 0; i < matrix.length; i++) {
            matrix[i] = Float.parseFloat(args[i + 1]);
            if (!Float.isFinite(matrix[i])) throw new IllegalArgumentException("Invalid calibration");
        }
        if (matrix[0] <= 0 || matrix[0] > 1 || matrix[4] <= 0 || matrix[4] > 1 ||
                matrix[1] != 0 || matrix[3] != 0 || matrix[2] < 0 || matrix[2] > 1920 ||
                matrix[5] < 0 || matrix[5] > 1920) throw new IllegalArgumentException("Invalid calibration");
        InputDevice synthetic = null;
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice device = InputDevice.getDevice(id);
            if (device != null && device.getName().equals("TeslaAndroid x86 Touchscreen")) {
                if (synthetic != null || device.getVendorId() != 0x5441 || device.getProductId() != 1 ||
                        !device.supportsSource(InputDevice.SOURCE_TOUCHSCREEN)) {
                    throw new IllegalStateException("Synthetic touchscreen identity mismatch");
                }
                synthetic = device;
            }
        }
        if (synthetic == null) throw new IllegalStateException("Synthetic touchscreen is unavailable");
        IBinder binder = (IBinder) Class.forName("android.os.ServiceManager")
            .getMethod("getService", String.class).invoke(null, "input");
        Object service = Class.forName("android.hardware.input.IInputManager$Stub")
            .getMethod("asInterface", IBinder.class).invoke(null, binder);
        Class<?> calibration = Class.forName("android.hardware.input.TouchCalibration");
        Object value = calibration.getConstructor(float.class, float.class, float.class,
            float.class, float.class, float.class).newInstance(matrix[0], matrix[1], matrix[2], matrix[3], matrix[4], matrix[5]);
        Class.forName("android.hardware.input.IInputManager")
            .getMethod("setTouchCalibrationForInputDevice", String.class, int.class, calibration)
            .invoke(service, synthetic.getDescriptor(), 0, value);
        System.out.println("OK");
    }

    private static void capture() throws Exception {
        Class<?> manager = Class.forName("android.os.ServiceManager");
        IBinder binder = (IBinder) manager.getMethod("getService", String.class).invoke(null, "window");
        if (binder == null) throw new IllegalStateException("WindowManager is unavailable");
        Class<?> window = Class.forName("android.view.IWindowManager");
        Object service = Class.forName("android.view.IWindowManager$Stub")
            .getMethod("asInterface", IBinder.class).invoke(null, binder);
        Method capture = null;
        for (Method method : window.getMethods()) {
            if (method.getName().equals("captureDisplay") && method.getParameterCount() == 3) {
                if (capture != null) throw new IllegalStateException("Ambiguous capture API");
                capture = method;
            }
        }
        if (capture == null) throw new IllegalStateException("Logical capture API is unavailable");
        Class<?> listenerType = capture.getParameterTypes()[2];
        Class<?> screenCapture = listenerType.getEnclosingClass();
        Object listener = screenCapture.getMethod("createSyncCaptureListener").invoke(null);
        capture.invoke(service, 0, null, listener);
        Class<?> synchronous = Class.forName(screenCapture.getName() + "$SynchronousScreenCaptureListener");
        Object screenshot = synchronous.getMethod("getBuffer").invoke(listener);
        if (screenshot == null) throw new IllegalStateException("Logical capture returned no buffer");
        HardwareBuffer buffer = (HardwareBuffer) screenshot.getClass().getMethod("getHardwareBuffer").invoke(screenshot);
        Bitmap bitmap = null;
        try {
            bitmap = (Bitmap) screenshot.getClass().getMethod("asBitmap").invoke(screenshot);
            if (bitmap == null || bitmap.getWidth() < 96 || bitmap.getHeight() < 96 ||
                    bitmap.getWidth() > 1920 || bitmap.getHeight() > 1920 ||
                    (long) bitmap.getWidth() * bitmap.getHeight() > 1600000) {
                throw new IllegalStateException("Invalid logical display dimensions");
            }
            if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, System.out)) {
                throw new IllegalStateException("PNG encoding failed");
            }
            System.out.flush();
        } finally {
            if (bitmap != null) bitmap.recycle();
            if (buffer != null) buffer.close();
        }
    }
}
