# Install DroidMate 0.1.2

You’ll set up Ubuntu, install DroidMate, then open Android in your browser. **You can skip the optional GPU encoding addon which is only tested on an Unraid VM.**

Already using DroidMate 0.1.1 or public RC17? Keep your Ubuntu VM and Android apps. [Start at Step 3](#3-download-droidmate).

## 1. Set up Ubuntu

A virtual machine (VM) is a computer that runs inside another computer. These steps use an Ubuntu VM.

1. Download the **64-bit PC (AMD64)** installer for **Ubuntu Desktop 26.04.1 LTS**. This works on both Intel and AMD PCs. [Download Ubuntu](https://releases.ubuntu.com/26.04/)
2. Give your new VM at least **6 GB of memory**, a **new 128 GB virtual disk**, and **bridged networking** so it gets its own address on your home network. Choose **VirtIO 3D / VirGL** graphics so Android can draw its screen. In Unraid, choose **Virtual** for the graphics card, **VNC** for the console, and **VirtIO(3D)** for the video driver. Pick your graphics device under **Render GPU**. [Unraid VM setup](https://docs.unraid.net/unraid-os/using-unraid-to/create-virtual-machines/vm-setup/)
3. Start the VM from the Ubuntu installer file. Open its console and follow the Ubuntu installer. If you choose **Erase disk and install Ubuntu**, check that the target is your new, empty virtual disk. Restart and sign in when Ubuntu finishes. [Ubuntu’s illustrated walkthrough](https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/)

If you want the shared Unraid GPU to handle video encoding on a new VM, follow the [Unraid GPU walkthrough](https://github.com/chilman408/DroidMate/blob/main/docs/UNRAID-GPU.md) here. Otherwise, go to Step 2.

## 2. Write down Ubuntu's address

Sign in to Ubuntu. Open **Terminal** with **Ctrl+Alt+T**, then run:

```sh
hostname -I
```

Write down the address that looks like `192.168.1.50`. Use the address you see in Ubuntu. This is how your browser finds your VM.

## 3. Download DroidMate

Copy one box at a time into the Ubuntu Terminal. Press **Enter** and wait for it to finish before moving to the next box. If Ubuntu asks for your password, type it and press **Enter**. The password stays hidden while you type.

```sh
sudo apt update && sudo apt install --yes ca-certificates curl python3 openssl
```

```sh
mkdir -p "$HOME/droidmate-0.1.2" && cd "$HOME/droidmate-0.1.2" &&
  curl --fail --location --output droidmate-0.1.2-install.pyz https://github.com/chilman408/DroidMate/releases/download/v0.1.2/droidmate-0.1.2-install.pyz
```

## 4. Check the download

```sh
printf '%s  %s\n' '49136260c84e2316962f6dcd9099add7414cdfe656bdd12aaee7744f0ea86217' 'droidmate-0.1.2-install.pyz' | sha256sum --check - &&
  python3 -I -B droidmate-0.1.2-install.pyz verify
```

This checks that the file matches the official download. Look for `droidmate-0.1.2-install.pyz: OK` and `droidmate-aggregate-installer-verified`. If a check fails, stop here. [Published checksums](https://github.com/chilman408/DroidMate/releases/download/v0.1.2/SHA256SUMS)

Already have DroidMate and unsure which version? Use `sudo python3 -I -B droidmate-0.1.2-install.pyz plan` to check the setup. The update below is for public 0.1.1 and RC17 installations. For other existing installs, save the output and ask for help.

## 5. Install or update

Pick **one** box in this section (see below) and run it in the same Terminal.

**New installation.** Replace `UBUNTU_IP` with the address you wrote down in Step 2. If your address is `192.168.1.50`, that’s what goes at the end of the command:

```sh
sudo python3 -I -B droidmate-0.1.2-install.pyz install-new "$(whoami)" UBUNTU_IP
```

**Update from 0.1.1 or public RC17.** Your Android apps, accounts and settings stay in place:

```sh
sudo python3 -I -B droidmate-0.1.2-install.pyz upgrade-existing
```

Leave the Terminal open. A new install may take 30 minutes or longer. When you see **`phase: accepted`**, you can open DroidMate. If you see an error instead, save it and see [If you get stuck](#if-you-get-stuck) before retrying.

## 6. Open DroidMate

1. On a device connected to the same home network, open your browser and type **`https://UBUNTU_IP:8443/`** into the address bar. For example, if your Ubuntu address is `192.168.1.50`, type `https://192.168.1.50:8443/`. If you updated, use your usual DroidMate address.
2. If the browser warns about the connection on a fresh install, check the certificate before you trust it. Run this command in Ubuntu and compare the fingerprint it prints with the SHA-256 fingerprint in your browser’s certificate details.

   ```sh
   openssl x509 -in "$HOME/.config/droiddeck/cert.pem" -noout -sha256 -fingerprint
   ```

3. You should see Android. Open an app and tap **Unmute** to check the sound. **The CPU can handle video encoding at this step.** To use it, open **Settings → Display** and choose **CPU Only** if you see a choice. Go back to the main **Settings** menu. **Encoding: CPU** below **Updates** tells you the CPU is doing the work.

**Want to use DroidMate away from home?** Follow [Open DroidMate from outside your home network](https://github.com/chilman408/DroidMate/blob/main/docs/REMOTE-ACCESS.md). It shows how to set up HTTPS with Synology or Nginx. Test a Tesla browser while parked.

## Optional GPU encoding

GPU encoding can compress video so the CPU has less to do. This option was tested in an **Ubuntu VM on Unraid**.

Other setups may work when Ubuntu offers **VA-API H.264 encoding** and DroidMate can access the GPU. If you run `vainfo` inside Ubuntu, look for `VAEntrypointEncSlice` or `VAEntrypointEncSliceLP` next to an H.264 profile. That tells you the driver offers encoding. To see whether DroidMate is using it, select **Automatic (GPU Preferred)** in **Settings → Display → Video Encoding** and look for **Encoding: GPU** while video plays. [VA-API capability examples](https://github.com/intel/libva-utils/blob/master/README.md#validate-your-environment)

[Turn on shared GPU encoding in Unraid](https://github.com/chilman408/DroidMate/blob/main/docs/UNRAID-GPU.md) walks through the addon one command at a time. Your Unraid console stays available.

## If you get stuck

- **The browser won't connect?** Check that the Ubuntu VM is on. Try its local address from the same home network, using `https://` and `:8443`.
- **The installer stopped or Android didn't come back after a restart?** Save the error and keep the VM and its files. If an update is recovering on its own, let it finish. [Recover an interrupted install or update](#recover-an-interrupted-install-or-update)
- **Need to undo an update?** Use the Undo steps for your version to keep your Android apps and data. [Undo an update](#undo-an-update)

## Recover an interrupted install or update

1. Let any running install or automatic recovery finish. Keep the same Ubuntu VM, disk and installer.
2. If DroidMate still won’t open, open Ubuntu Terminal. Pick the command for what you were doing.

   **Updating an existing installation**

   ```sh
   cd "$HOME/droidmate-0.1.2" && sudo python3 -I -B droidmate-0.1.2-install.pyz upgrade-recover
   ```

   **Installing on fresh Ubuntu**

   ```sh
   cd "$HOME/droidmate-0.1.2" && sudo python3 -I -B droidmate-0.1.2-install.pyz recover
   ```

3. If you see `accepted` or `restored`, try your usual DroidMate address. If recovery refuses or the new install stopped before the installer saved its recovery information, keep the files and ask for help with the error.

## Undo an update

To undo a completed update, run this in Ubuntu Terminal.

```sh
cd "$HOME/droidmate-0.1.2" && sudo python3 -I -B droidmate-0.1.2-install.pyz upgrade-undo
```

On a completed fresh install, use `undo` in place of `upgrade-undo`. Undo brings back the preserved CPU streaming setup and keeps Ubuntu and your Android apps and data. Wait for `restored`, then check picture and sound in the browser.
