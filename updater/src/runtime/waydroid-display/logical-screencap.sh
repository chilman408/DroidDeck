#!/system/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# The stream launcher supplies the validated image boot classpaths.
[ "$#" -eq 1 ] && [ "$1" = "-p" ] || exit 2
[ -n "$BOOTCLASSPATH" ] && [ -n "$DEX2OATBOOTCLASSPATH" ] || exit 2
unset LD_LIBRARY_PATH
exec /system/bin/env ANDROID_ROOT=/system ANDROID_DATA=/data ANDROID_STORAGE=/storage \
  ANDROID_ART_ROOT=/apex/com.android.art ANDROID_I18N_ROOT=/apex/com.android.i18n \
  ANDROID_TZDATA_ROOT=/apex/com.android.tzdata \
  CLASSPATH=/data/local/tmp/tesla-waydroid-test-20261003/logical-capture.jar \
  /system/bin/app_process /system/bin TeslaLogicalCapture
