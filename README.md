# DroidMate

**Run Android on an Ubuntu computer. Use it from your browser.**

DroidMate grew out of [TeslaAndroid](https://github.com/tesla-android), which showed how an Android screen running on a Raspberry Pi 4 could be used from a Tesla's browser. DroidMate is a complete rewrite of that idea for 64-bit Intel and AMD computers running Ubuntu. It uses [Waydroid](https://waydro.id/) to run Android and removes the need to own a Raspberry Pi 4. [Original TeslaAndroid Pi 4 guide](https://github.com/tesla-android/tesla-android.github.io/blob/main/install-guide.md)

## What does it do?

Think of it as an Android tablet that lives on your Ubuntu computer. Open DroidMate in a browser to see the Android screen, tap or click to control apps, and listen to their audio. You can use a regular desktop browser or a compatible Tesla browser while parked. Your apps, sign-ins and settings live on the Ubuntu machine running Android.

## What's improved in DroidMate?

- **Flexible hardware.** The supported setup runs on an x86 Ubuntu VM and can share a compatible server GPU with other workloads.
- **Streaming that adapts.** DroidMate adjusts video bitrate and frame rate as the connection changes while keeping your chosen resolution. It offers AAC and FLAC audio and two H.264 playback modes, WebCodecs and Legacy.
- **GPU encoding with recovery.** On the qualified setup, the GPU can compress the video stream. The CPU takes over if encoding fails, and DroidMate automatically tries the GPU again.
- **In-place updates.** The signed 0.1.1 installer can update an existing public RC17 installation and preserve apps, Android data and settings.

## Install DroidMate

**[Follow the numbered installation guide](INSTALL.md)** to set up Ubuntu, download and verify the installer, and open DroidMate in your browser. The guide covers which Ubuntu environments are supported, what a GPU needs for hardware encoding, and how to enable the shared GPU in an Unraid VM.

The tested 0.1.1 setup is **Ubuntu Desktop 26.04.1 LTS (x86-64)** in a **VirtIO 3D / VirGL VM on Unraid**. The guide explains the requirements for other Linux and Windows setups.

[Download DroidMate 0.1.1](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.1) · [Release notes](https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/RELEASE-NOTES.md) · [Test results](https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/QUALIFICATION.json)

## Source and license

The repository URL and some service names retain the earlier DroidDeck name. The app is DroidMate. Its source is [GPL-3.0-only](LICENSE), and upstream components retain their licenses. See the [0.1.1 source snapshot](https://github.com/chilman408/DroidDeck/tree/v0.1.1/release-0.1.1) and [security reporting](SECURITY.md). DroidMate is an independent project.
