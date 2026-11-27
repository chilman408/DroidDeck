# DroidMate

**Run Android on an Ubuntu computer. Use it from your browser.**

DroidMate grew out of [TeslaAndroid](https://github.com/tesla-android), which showed how an Android screen running on a Raspberry Pi 4 could be used from a Tesla's browser. DroidMate is a complete rewrite of that idea for 64-bit Intel and AMD computers running Ubuntu. It uses [Waydroid](https://waydro.id/) to run Android and removes the need to own a Raspberry Pi 4. [Original TeslaAndroid Pi 4 guide](https://github.com/tesla-android/tesla-android.github.io/blob/main/install-guide.md)

## What does it do?

Think of it as an Android tablet that lives on your Ubuntu computer. Open DroidMate in a browser to see the Android screen, tap or click to control apps, and listen to their audio. You can use a regular desktop browser or a compatible Tesla browser while parked. Your apps, sign-ins and settings live on the Ubuntu machine running Android.

## What's improved in DroidMate?

- **Flexible hardware.** The supported setup runs on an x86 Ubuntu environment.
- **Streaming that adapts.** DroidMate adjusts video bitrate and frame rate as the connection changes while keeping your chosen resolution. It offers AAC and FLAC audio and two H.264 playback modes, WebCodecs and Legacy.
- **GPU encoding with recovery.** On the qualified setup, the GPU can compress the video stream. The CPU takes over if encoding fails, and DroidMate automatically tries the GPU again.
- **In-place updates.** Supported updates keep your Android apps, data, and settings.

## Install DroidMate

**[Follow this installation guide](INSTALL.md)** to set up Ubuntu, download and verify the installer, and open DroidMate in your browser. GPU encoding is optional and was tested in an Ubuntu VM on Unraid.

[Download DroidMate 0.1.5](https://github.com/chilman408/DroidMate/releases/tag/v0.1.5) · [Release notes](https://github.com/chilman408/DroidMate/releases/download/v0.1.5/RELEASE-NOTES.md) · [Test results](https://github.com/chilman408/DroidMate/releases/download/v0.1.5/QUALIFICATION.json)

## Source and license

Some service names still use the original DroidDeck name for compatibility. Its source is [GPL-3.0-only](LICENSE), and upstream components retain their licenses. See the [0.1.5 source snapshot](https://github.com/chilman408/DroidMate/tree/v0.1.5/release-0.1.5) and [security reporting](SECURITY.md). DroidMate is an independent project.

See the [current browser renderer guide](docs/BROWSER-RENDERER.md), [supported setups and limitations](docs/KNOWN_LIMITATIONS.md), or [earlier releases](https://github.com/chilman408/DroidMate/releases). The [pilot files and source](https://github.com/chilman408/DroidMate/tree/f1272af682b9a415eb3158d1bd7f7bac5bb1fe50) are available in the last main-branch snapshot that contained them.
