# Supported setups and limitations

- The [installation guide](../INSTALL.md) uses 64-bit Ubuntu 26.04.1. DroidMate needs working Android graphics on Ubuntu. Video encoding can run on the CPU.
- [Optional GPU encoding](UNRAID-GPU.md) was tested in an Ubuntu VM on Unraid using VirtIO 3D. On other setups, check for H.264 encoding support in VA-API and confirm **Encoding: GPU** under **Updates** while the video plays.
- A fresh installation downloads Android and other inputs from their publishers. If a required download is unavailable or its checksum changes, the installer stops.
- Browser audio starts after you tap **Unmute**. Poor connections can still cause playback gaps. [Choose an H.264 renderer](BROWSER-RENDERER.md) if your browser has video trouble.
- Android app compatibility varies. Apps can require a secure lockscreen. Supported updates preserve the existing lockscreen setting.
- Local browser access uses HTTPS. For access away from home, follow the [remote access guide](REMOTE-ACCESS.md). DroidMate starts with Browser Login off.
