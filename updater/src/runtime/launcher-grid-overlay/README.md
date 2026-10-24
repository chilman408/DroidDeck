# Trebuchet Tablet Grid Compatibility

This is a resource-only overlay for the exact Android16 WayDroid-ATV Trebuchet
image documented in `docs/feasibility/three-button-navigation.md`. It changes the
tablet `6_by_5` grid's `isScalable` flag to false to avoid repeated inline-nav
hotseat shrinking to zero. It does not replace/re-sign Trebuchet, clear launcher
data, change the Home app, hide navigation or introduce a streaming fallback.

The XML here is only a build placeholder. Never install an APK containing it.
The generator rejects any original grid resource whose SHA256 differs from the
pinned fixture. It retains all original compiled attribute/resource references.

## Build

Use a private staging directory, Android `aapt2`/`apksigner`, a framework resource
APK and Python with `tools/taskbar-profile-requirements.txt` installed.

```sh
aapt2 compile --dir runtime/launcher-grid-overlay/res -o compiled.zip
aapt2 link -I /usr/share/android-framework-res/framework-res.apk \
  --manifest runtime/launcher-grid-overlay/AndroidManifest.xml \
  --package-id 0x80 -o template.apk compiled.zip
python tools/taskbar_profile.py original-trebuchet.apk unsigned-overlay.apk \
  --template template.apk
apksigner sign --ks YOUR_OVERLAY_KEYSTORE --out overlay.apk unsigned-overlay.apk
apksigner verify --verbose --print-certs overlay.apk
```

Package ID0x80 keeps the preserved original target references distinct from the
overlay's own resource table. The overlay has no code or Android permissions.
The signing key belongs only to this separate overlay; it is not a platform key.

## Deployment And Rollback

Normal app installation is rejected by Android's target-signature policy for
this target. Do not bypass that check. Stage the separate overlay through
Waydroid's supported root filesystem overlay directory, at:
`/var/lib/waydroid/overlay/system/system_ext/overlay/TeslaTabletGrid/TeslaTabletGrid.apk`.
Keep parent directories root0755 and the APK root0644. Preserve the current
preferences and original image. Restart the container/session in a controlled
maintenance window, then enable only `org.teslaandroid.compat.trebuchetgrid` for
user0 using `cmd overlay enable`. Verify actual grid counts, drawer clicks and
Back/Home/Recents across narrow/wide/portrait before accepting the change.

Rollback: disable only this overlay, move only this APK into a private backup,
restart the container/session and verify the original resource is selected.
Never remove other overlays or the original launcher. Before changing/upgrading
the Android image, disable/remove this version-specific overlay and rebuild only
if the new image's exact grid is supported; do not assume resource IDs are stable.

Fabricated XML (`cmd overlay fabricate ... drawable ...`) was rejected by live
XML loading; the disabled `com.android.shell:TeslaTabletGrid` trial must not be
re-enabled. The packaged overlay is a separate approach.
