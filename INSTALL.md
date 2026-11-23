# Install DroidMate 0.1.1

**For CPU encoding, finish Step 5 and open DroidMate in Step 6.** When the installer reports `phase: accepted`, the video stream is ready. If hardware encoding is unavailable, DroidMate can use the CPU. The Unraid hardware encoder addon is optional.

**The fresh installer still needs working VirGL 3D graphics inside Android.** 3D graphics draws the Android screen. Video encoding compresses that screen for the browser. CPU encoding handles the second job.

If you already run public RC17, keep your installation and [start at Step 3](#3-find-your-address-and-download-droidmate).

## 1. Choose where Ubuntu will run

Use **Ubuntu Desktop 26.04.1 LTS, 64-bit PC (AMD64)** for a new VM. AMD64 works on both Intel and AMD x86-64 PCs. [Ubuntu downloads](https://releases.ubuntu.com/26.04/)

| Where Ubuntu runs | When you can use CPU encoding |
| --- | --- |
| **Unraid VM with VirtIO 3D / VirGL** | **Step 6**, after Step 5 reports `accepted`. CPU encoding was verified on Unraid. A fresh install without the optional encoder addon follows the same installer path if it passes the VirGL checks. That exact setup was not separately release-tested. |
| **Proxmox VM** | **Step 6 if the install succeeds**, including the installer’s VirGL checks. This host is unverified for 0.1.1. [Proxmox VM graphics settings](https://pve.proxmox.com/wiki/Manual:_qm.conf) |
| **TrueNAS VM** | **Step 6 if the install succeeds**, including the installer’s VirGL checks. This host is unverified for 0.1.1. [TrueNAS VM setup](https://www.truenas.com/docs/scale/virtualmachines/managingvms/) |
| **Ubuntu directly on a standalone PC** | **No supported install step in 0.1.1.** A direct install normally exposes the physical GPU driver. The fresh installer expects Android to report VirGL. A PC hosting a compatible Ubuntu VM follows the VM route. |
| **Ubuntu under Windows WSL** | **No supported install step in 0.1.1.** WSL’s systemd and GPU features alone don’t establish Waydroid and VirGL compatibility. You can use a Windows browser to open DroidMate running on a compatible Ubuntu VM. [WSL systemd](https://learn.microsoft.com/en-us/windows/wsl/systemd/) · [Waydroid kernel requirements](https://docs.waydro.id/debugging/getting-essential-information) |

## 2. Install and start Ubuntu

1. Create a VM. Use **6 GB RAM** or more, a **new 128 GB virtual disk**, and a bridged network so it gets its own address on your local network. Attach the Ubuntu ISO as the boot media.
2. On Unraid, choose **VMs → Add VM → Ubuntu**. Set **Graphics Card** to **Virtual**, **VM Console Protocol** to **VNC**, **VM Console Video Driver** to **VirtIO(3D)**, and select your compatible shared AMD device under **Render GPU**. Keep that VM name handy. [Unraid VM setup](https://docs.unraid.net/unraid-os/using-unraid-to/create-virtual-machines/vm-setup/)
3. Open the VM console and choose **Try or Install Ubuntu**, then **Install Ubuntu → Interactive installation**. Select the **new VM disk** and create a normal Ubuntu user. Only select **Erase disk and install Ubuntu** for the new, empty virtual disk. [Ubuntu's illustrated installer guide](https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/)
4. When Ubuntu finishes installing, disconnect the ISO or put the virtual disk first in the boot order. Restart, sign in, and check that a website opens.
5. To stream with CPU encoding, go straight to Step 3. To use the tested shared hardware encoder, start with the [Unraid GPU example](#optional-unraid-shared-gpu-example) and then return to Step 3.

## 3. Find your address and download DroidMate

In Ubuntu, open **Terminal** with **Ctrl+Alt+T**. Run:

```sh
whoami
hostname -I
```

Write down Ubuntu's private IPv4 address, for example `192.168.1.50`. In a VM, use the guest's address. When `sudo` asks for your Ubuntu password, nothing appears on screen as you type.

Run these commands in the same Ubuntu Terminal:

```sh
sudo apt update && sudo apt install --yes ca-certificates curl python3 openssl
```

```sh
mkdir -p "$HOME/droidmate-0.1.1" && cd "$HOME/droidmate-0.1.1" &&
  curl --fail --location --output droidmate-0.1.1-install.pyz https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/droidmate-0.1.1-install.pyz
```

## 4. Check the download and choose the install path

Run this in the same download folder. The first check should say **droidmate-0.1.1-install.pyz: OK**. The second checks the embedded signature and should report `droidmate-aggregate-installer-verified`. Stop and check the download if either fails. [Published checksums](https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/SHA256SUMS)

```sh
printf '%s  %s\n' '37913bc0a263b99d85001f981e5425370e4ec9b0e934fd9ca492aee735fa8350' 'droidmate-0.1.1-install.pyz' | sha256sum --check - &&
  python3 -I -B droidmate-0.1.1-install.pyz verify
```

Now check what the installer sees:

```sh
sudo python3 -I -B droidmate-0.1.1-install.pyz plan
```

- **`layout: clean`** means use the new install below. `readyForInstallation: false` is expected at this point.
- **`layout: portable`** on a working public RC17 setup means use the update below. Keep any existing GPU profile and its recovery notes.
- If you see **`recognized-legacy`**, an unfamiliar existing install, or a refusal, keep the message and existing files. The commands below are for fresh Ubuntu and public RC17.

## 5. Install or update

Run **one** command in Ubuntu Terminal.

**New installation.** Replace `UBUNTU_IP` with the IPv4 address from Step 3:

```sh
sudo python3 -I -B droidmate-0.1.1-install.pyz install-new "$(whoami)" UBUNTU_IP
```

**Updating public RC17.** This keeps your Android apps, accounts, lockscreen, browser settings and TLS identity:

```sh
sudo python3 -I -B droidmate-0.1.1-install.pyz upgrade-existing
```

Keep Ubuntu running while it works. A fresh install downloads Android images and dependencies from their publishers and may take 30 minutes or longer. Wait for a final report with **`phase: accepted`**. If it stops or fails, keep the error and consult [recovery and Undo](#recovery-and-undo).

## 6. Open DroidMate in your browser

1. On your computer or another device on the same network, open `https://UBUNTU_IP:8443/` using the address from Step 3. For example, `https://192.168.1.50:8443/`. If you're updating and already have a working HTTPS hostname, use that address.
2. A fresh install uses a local HTTPS certificate. If your browser warns about it, use Ubuntu Terminal to display the certificate fingerprint. Compare it to the SHA-256 fingerprint in your browser before trusting it. If your browser cannot trust the local certificate, use a reverse proxy with a trusted certificate.

   ```sh
   openssl x509 -in "$HOME/.config/droiddeck/cert.pem" -noout -sha256 -fingerprint
   ```

3. When Android appears, complete the first-run setup. Open an app and tap **Unmute** to check sound. Restart Ubuntu normally and open DroidMate again to check that it comes back.
4. Open **Settings → Display**. For CPU encoding, choose **CPU Only** if a choice is available. Look for **Encoding: CPU** at the bottom while the picture is moving.

To try a Tesla browser while parked, use an HTTPS hostname the car can reach and a certificate it trusts. Start on the local network. For remote use, set up protected access and enable browser login first. Browser login starts off.

## Optional GPU encoding check

Run `vainfo` **inside the Ubuntu environment running DroidMate**. For a VM, that means inside the guest. Install the matching Ubuntu VA-API driver for your device if needed.

```sh
sudo apt update && sudo apt install --yes vainfo
vainfo --display drm --device /dev/dri/renderD128
```

Substitute the render device path if yours differs. An H.264 profile with **`VAEntrypointEncSlice`** or **`VAEntrypointEncSliceLP`** means the driver advertises H.264 encoding. **`VAEntrypointVLD`** indicates decoding. [VA-API capability examples](https://github.com/intel/libva-utils/blob/master/README.md#validate-your-environment)

Seeing H.264 encode support makes the environment a candidate for hardware encoding, including outside Unraid. DroidMate still needs to finish installation, its service needs access to the GPU, and the encoder needs to start successfully with the stream's settings.

To verify actual use, select **Automatic (GPU Preferred)** in **Settings → Display → Video Encoding** if available. With video playing, look for **Encoding: GPU** at the bottom of Display. **Encoding: CPU** means the current stream is using software encoding.

## Optional Unraid shared GPU example

The tested example uses **Unraid 7.1.4, QEMU 9.2.3, a compatible shared AMD GPU, and Virtual / VNC / VirtIO(3D)**. The addon checks the exact host setup and changes the launcher for just the selected VM. It keeps VNC available.

For a new Ubuntu VM, do this after Step 2 if you want the tested GPU path:

1. With the VM still running, open **Unraid Terminal**. Use the release's commands to download, verify and prepare the **V2 host addon** for that VM. Save the profile hash the tool prints.
2. Shut down only the selected VM. Use the same terminal to attach the prepared profile and start the VM again. Keep the profile details for Undo.
3. Return to [Step 3](#3-find-your-address-and-download-droidmate). Once the stream is open, confirm **Encoding: GPU** while video is playing.

[Exact Unraid addon commands and checks](https://github.com/chilman408/DroidDeck/blob/v0.1.1/release-0.1.1/INSTALL.md#part-3-prepare-shared-gpu-encoding)

If your VM already has a working GPU addon, keep the existing profile and use the versioned guide for upgrade or Undo.

## Recovery and Undo

- If your browser cannot connect, check that Ubuntu is running, use its current address, and try from the same LAN first. The URL needs **`https://`** and **`:8443`**.
- If an installation stops, preserve its files and logs. Let any automatic recovery finish before using the matching installer's recovery command.
- To undo a guest update or remove an Unraid profile, use the release's [recovery and Undo instructions](https://github.com/chilman408/DroidDeck/blob/v0.1.1/release-0.1.1/INSTALL.md#if-something-goes-wrong). Restore the guest CPU runtime successfully before undoing the GPU profile on Unraid.

[Installation guide as shipped with 0.1.1](https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/INSTALL.md) · [Release downloads](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.1)
