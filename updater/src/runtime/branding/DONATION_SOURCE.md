# Donation Source

Copied from the user's Android 15 RPi4 project, not the upstream TeslaAndroid
donation destination. Message requested for reuse verbatim:

This is a self funded project, your contribution means the world to me!

Source repository: TeslaAndroid-R15-Rpi4 local android-raspberry-pi checkout.
Patch: patches-aosp/vendor/tesla-android/0002-release-Brand-Android-15-work-week-39.patch.
Asset: services/lighttpd/www-default/beta/assets/donations-qr.svg.
Expected source MD5 in tools/tests/test_android15_release_presentation.py:
`3450073a38e6d616d6ce6cdcaf9d0c22` (LF-normalized original bytes match).

`donations-qr.svg` here is that exact patched asset, extracted by applying only
its unified-diff file section to a separate copy of the original asset. No QR
regeneration or changes to the source RPi4 project were made. CRLF normalized
to LF; original matrix, metadata and dimensions preserved.

Destination, verified from the matrix using jsQR 1.4.0 after rendering with
Sharp, and again from browser-rendered screenshots at widths 904/390/320:

https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0&item_name=Thank+you+for+your+support%21++I+appreciate+it.&currency_code=USD

Asset SHA256:
`1935564279df3fa85e4fa93f4288d19a05d1b3c27f61ae6572039773f7deeee0`.
The browser link matches the decoded destination. No payment was submitted;
automated checks do not follow the external payment link.
