# Install DroidMate 0.1.1

Follow Steps 1–6 to go from an Ubuntu computer to Android in your browser. If you already run the public RC17 release, keep your existing installation and start at Step 3.

## 1. Choose where Ubuntu will run

The installer uses **64-bit Ubuntu 26.04** on an Intel or AMD computer. The tested release uses **Ubuntu Desktop 26.04.1 LTS**, a normal Ubuntu user with administrator access, and hardware-accelerated **VirtIO 3D / VirGL** graphics for Android. Download the **AMD64 desktop ISO** for a VM. AMD64 is the download name for both Intel and AMD x86-64 PCs. [Ubuntu downloads](https://releases.ubuntu.com/26.04/)

| Where you want to run it | What to do for 0.1.1 |
| --- | --- |
| **Ubuntu VM on Unraid** | Use the tested configuration. Create a VM using the settings below, then follow the shared GPU example before running the installer. |
| **Ubuntu VM on Proxmox or TrueNAS** | A full Ubuntu VM with working VirGL and a reachable LAN address is a candidate for testing. These platforms have yet to be verified for 0.1.1. Use a disposable guest for evaluation. [Proxmox VM configuration](https://pve.proxmox.com/wiki/Manual:_qm.conf) · [TrueNAS VM setup](https://www.truenas.com/docs/scale/virtualmachines/managingvms/) |
| **Standalone x86 PC** | Use a supported Ubuntu VM environment on this PC or another machine. A direct Ubuntu install normally exposes the physical GPU driver, while the 0.1.1 installer explicitly checks for VirGL in Android. Direct bare-metal setup has yet to be supported. |
| **Windows with WSL** | A WSL Ubuntu distribution is outside the 0.1.1 tested setup. WSL's systemd and GPU features alone do not establish Waydroid kernel or VirGL compatibility. From Windows, use your browser to reach a supported Ubuntu VM. [WSL systemd](https://learn.microsoft.com/en-us/windows/wsl/systemd/) · [Waydroid kernel requirements](https://docs.waydro.id/debugging/getting-essential-information) |

3D graphics lets Android draw its screen. Video encoding compresses that screen for the browser. 0.1.1 checks for working VirGL graphics during fresh installation. CPU fallback applies to **video encoding** on an otherwise working installation.

## 2. Install and start Ubuntu

1. Create a VM. Use **6 GB RAM** or more, a **new 128 GB virtual disk**, and a bridged network so it gets its own address on your local network. Attach the Ubuntu ISO as the boot media.
2. On Unraid, choose **VMs → Add VM → Ubuntu**. Set **Graphics Card** to **Virtual**, **VM Console Protocol** to **VNC**, **VM Console Video Driver** to **VirtIO(3D)**, and select your compatible shared AMD device under **Render GPU**. Keep that VM name handy. [Unraid VM setup](https://docs.unraid.net/unraid-os/using-unraid-to/create-virtual-machines/vm-setup/)
3. Open the VM console and choose **Try or Install Ubuntu**, then **Install Ubuntu → Interactive installation**. Select the **new VM disk** and create a normal Ubuntu user. Only select **Erase disk and install Ubuntu** for the new, empty virtual disk. [Ubuntu's illustrated installer guide](https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/)
4. When Ubuntu finishes installing, disconnect the ISO or put the virtual disk first in the boot order. Restart, sign in, and check that a website opens.
5. On a new Unraid VM, follow [the shared GPU example](#unraid-example-enable-shared-gpu-encoding) below, then return to Step 3. For another host, see [GPU encoding on Ubuntu](#gpu-encoding-on-ubuntu) for the requirements.

## 3. Find your address and download DroidMate

In Ubuntu, open **Terminal** with **Ctrl+Alt+T**. Run:

```sh
whoami
hostname -I
```

Write down your Ubuntu username and the Ubuntu computer's private IPv4 address, for example `192.168.1.50`. Use the guest's address if you're in a VM. Reserving it in your router keeps the browser address stable. When `sudo` asks for your Ubuntu password, nothing appears on screen as you type.

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
4. To try a Tesla browser while parked, use an HTTPS hostname the car can reach and a certificate it trusts. Start on the local network. For remote use, set up protected access and enable browser login before exposing the service. Browser login starts off, and the installer does not configure remote access.

## GPU encoding on Ubuntu

Hardware H.264 encoding requires a GPU with H.264 encode support, a matching Linux VA-API driver, and permission for the DroidMate service to use the GPU. Install the driver for your GPU from Ubuntu's package sources, then check that Ubuntu can see its render device. In a VM, the guest also needs a virtual graphics path that exposes **video encoding**. A 3D-accelerated desktop can work even when video encoding is unavailable.

Inside Ubuntu, check the render device and its VA-API capabilities:

```sh
ls -l /dev/dri/
sudo apt update && sudo apt install --yes vainfo
vainfo --display drm --device /dev/dri/renderD128
```

If your GPU uses a different `renderD*` node, substitute that path. Look for an H.264 profile with an encoding entry point such as `VAEntrypointEncSlice` or `VAEntrypointEncSliceLP`. This is a capability check. Confirm actual use in **Settings → Display → Video Encoding**, with the mode set to **Automatic**. A GPU that passes `vainfo` still needs to meet DroidMate's graphics and runtime checks. **CPU Only** is available on a working installation when you prefer software encoding. [VA-API diagnostic tools](https://github.com/intel/libva-utils)

The 0.1.1 shared encoding addon is specific to the Unraid setup below. Use the generic commands to check another Ubuntu environment's capabilities. The Unraid addon cannot configure a different host or WSL.

## Unraid example: enable shared GPU encoding

This is the tested **Unraid 7.1.4 / QEMU 9.2.3** setup with a compatible AMD render device and an Ubuntu guest using **Virtual / VNC / VirtIO(3D)**. The addon checks the exact host components and device. It keeps VNC and changes the launcher for one VM. Start these steps with a new VM running stock graphics. If you already have a working GPU addon or the checks refuse your host, keep your setup and consult the [versioned guide](https://github.com/chilman408/DroidDeck/blob/v0.1.1/release-0.1.1/INSTALL.md#part-3-prepare-shared-gpu-encoding) before changing anything.

1. Open **Terminal in the Unraid web interface**, with the Ubuntu VM still running. Download and check the addon:

   ```sh
   work=$(mktemp -d /tmp/droidmate-0.1.1.XXXXXX) && cd "$work" &&
     curl --fail --location --output droidmate-gpu-host-v2.tar https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/droidmate-gpu-host-v2.tar &&
     printf '%s  %s\n' '0e8ad94c23fb704439eeac7a72ea0970a896b8fa5222c9bbf7b647092199f06f' 'droidmate-gpu-host-v2.tar' | sha256sum --check -
   ```

   Continue when the checksum check reports **`droidmate-gpu-host-v2.tar: OK`**.

2. In the same Unraid Terminal, replace `YOUR_VM_NAME` with the exact Unraid VM name and run:

   ```sh
   vm_name='YOUR_VM_NAME'
   vm_uuid=$(virsh domuuid "$vm_name") &&
     generation=$(date -u +%Y%m%d%H%M%S) &&
     addon="/etc/libvirt/droidmate-gpu-addon-official-$generation" &&
     profile="/etc/libvirt/droidmate-qemu-launcher-profile-official-$generation" &&
     manifest_sha=aee2d8fea3eba21cc1ddeceeb5caaa371595dd675fcbe982d2b2777dc73c93bb &&
     test ! -e "$addon" && test ! -e "$profile" &&
     mkdir -m 700 "$addon" &&
     (umask 022; tar -xf droidmate-gpu-host-v2.tar -C "$addon") &&
     php "$addon/source/tools/qemu-video-addon-prepare.php" "$addon" "$manifest_sha" "$vm_uuid" "$profile" plan &&
     php "$addon/source/tools/qemu-video-addon-prepare.php" "$addon" "$manifest_sha" "$vm_uuid" "$profile" prepare
   ```

   Look for **`prepared: true`** and save the **`manifestSha256`** from the result. Keep this Terminal open so the VM and profile variables are still available. Stop if preparation fails.

3. From the Unraid VMs page, shut down **only this Ubuntu VM** and wait for it to stop. Back in the same Unraid Terminal, paste the profile hash you saved:

   ```sh
   profile_sha='PASTE_PROFILE_HASH_HERE'
   virsh domstate "$vm_uuid"
   ```

   When the state says **`shut off`**, attach the profile and start the VM:

   ```sh
   php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" plan &&
     php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" install &&
     php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" status &&
     virsh start "$vm_uuid"
   ```

4. Look for **`phase: installed`**, then open the VM's VNC console and sign in to Ubuntu. Privately save these four values for Undo:

   ```sh
   printf 'VM_UUID=%s\nADDON=%s\nPROFILE=%s\nPROFILE_SHA256=%s\n' "$vm_uuid" "$addon" "$profile" "$profile_sha"
   ```

5. Go back to [Step 3](#3-find-your-address-and-download-droidmate). Once DroidMate is playing in the browser, open **Settings → Display** and check that **Video Encoding** shows Automatic and the status reports GPU encoding while video is running.

## Recovery and Undo

- If your browser cannot connect, check that Ubuntu is running, use its current address, and try from the same LAN first. The URL needs **`https://`** and **`:8443`**.
- If an installation stops, preserve its files and logs. Let any automatic recovery finish before using the matching installer's recovery command.
- To undo a guest update or remove an Unraid profile, use the release's [recovery and Undo instructions](https://github.com/chilman408/DroidDeck/blob/v0.1.1/release-0.1.1/INSTALL.md#if-something-goes-wrong). Restore the guest CPU runtime successfully before undoing the GPU profile on Unraid.

[Installation guide as shipped with 0.1.1](https://github.com/chilman408/DroidDeck/releases/download/v0.1.1/INSTALL.md) · [Release downloads](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.1)
