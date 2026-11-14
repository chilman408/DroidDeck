# Install DroidMate RC17: a step-by-step guide

This guide takes you from an empty Ubuntu virtual machine to Android running in your browser. It also explains how to update an existing DroidMate installation.

**Already running RC17 successfully? You are finished. You do not need to reinstall it.**

## Before you begin

A **virtual machine**, or **VM**, is a small computer running inside your Unraid server. You will use two different screens:

| Label in this guide | Where to type |
| --- | --- |
| **Ubuntu Terminal** | Open your Ubuntu desktop through Unraid's VNC console. Press **Ctrl + Alt + T**. |
| **Unraid Terminal** | Open your Unraid web page on your computer. Click the **Terminal** icon at the top. |

Copy commands from one box at a time. Press **Enter** after pasting. Do not type the three backticks around a command box.

When Ubuntu asks for your password, type your **Ubuntu password** and press Enter. Nothing appears while you type, not even dots. This is normal. `sudo` simply asks Ubuntu to run that command with administrator permission.

### Which route should you follow?

- **Brand-new installation:** start at Step 1.
- **Updating a working DroidMate/DroidDeck VM:** keep its disk and Android data. Start at Step 4. Do not reinstall Ubuntu.

For this pilot, the tested GPU setup is **Unraid 7.1.4, QEMU 9.2.3 and the qualified shared AMD GPU setup**. Matching version numbers alone do not guarantee compatibility: the GPU installer checks the actual host files and device too. If it refuses your setup, stop and keep the error message. Do not change drivers or permissions to force it through.

## Part 1: create the Ubuntu computer

**Updating an existing VM? Skip this part and go to Step 4.**

### Step 1: get Ubuntu

On your regular computer:

1. Get the **Ubuntu Desktop 26.04.1 LTS, AMD64/x86_64** ISO from [Ubuntu's official downloads](https://releases.ubuntu.com/26.04/). Use this qualified version for RC17.
2. An ISO is an installation-disc file. Put it in your Unraid **isos** share, using the same method you use to copy other files to Unraid.
3. Open your Unraid web page and choose **VMs**, then **Add VM**, then the **Ubuntu** template.

### Step 2: create a new VM

Give the VM a clear name, such as `DroidMate`. Write down the exact name, including spaces and capital letters. You will need it later.

Set these options in the VM editor. Enable **Advanced View** if an option is hidden.

| Setting | Choose |
| --- | --- |
| Memory | At least **6 GB** |
| Primary virtual disk | A **new, empty disk**, at least **128 GB** |
| OS installation ISO | The Ubuntu ISO from Step 1 |
| Graphics Card | **Virtual** |
| VM Console Protocol | **VNC** |
| VM Console Video Driver | **VirtIO(3D)** |
| Render GPU | Your supported shared AMD GPU |
| Network | Your normal LAN bridge, so Ubuntu gets its own LAN IP address |

Keep the template's other defaults unless your Unraid setup requires something else. Do not select another VM's disk or pass the GPU through exclusively to this VM.

Create and start the VM. Open its **VNC** console. You should see Ubuntu's installer.

Unraid's [official VM setup guide](https://docs.unraid.net/unraid-os/using-unraid-to/create-virtual-machines/vm-setup/) explains these graphics and console options.

### Step 3: install Ubuntu

Inside the new VM's VNC window:

1. Select **Try or Install Ubuntu** if a boot menu appears.
2. Choose **Install Ubuntu**, then **Interactive installation**.
3. Choose your language, keyboard, time zone and normal application selection.
4. On the disk screen, choose **Erase disk and install Ubuntu** **only for this new VM's empty virtual disk**. Never do this when updating a working VM.
5. Create your first normal Ubuntu account. Write down its username and password privately. Do not use `root` as the account name.
6. Review the disk choice, then click **Install**.
7. When installation finishes, restart the VM. In Unraid, disconnect the installation ISO or put the virtual disk first in the boot order. The next boot should reach the installed Ubuntu desktop, not the installer again.
8. Sign in to Ubuntu. Check that Firefox can open a website.

You can follow [Ubuntu's illustrated installation guide](https://ubuntu.com/desktop/docs/en/latest/tutorial/install-ubuntu-desktop/) if you need help with a screen.

## Part 2: download and check DroidMate

### Step 4: write down the VM's address

**Where: Ubuntu Terminal.** Open it with **Ctrl + Alt + T**.

Run:

```sh
whoami
hostname -I
```

The first command shows your Ubuntu username. The second shows network addresses. Write down the VM's **private IPv4 address**, for example `192.168.1.50`. Do not use Unraid's address, an address containing colons, or the example address unless it is actually yours.

Keep this address stable with a DHCP reservation in your router. DroidMate uses it for its local HTTPS identity. The router's instructions will call this an address reservation, static lease or DHCP reservation.

**If copying long commands into VNC is awkward:** you can use an SSH connection instead. SSH opens the Ubuntu Terminal from your regular computer. In the VM's Ubuntu Terminal, run:

```sh
sudo apt update && sudo apt install --yes openssh-server && sudo systemctl enable --now ssh
ssh-keygen -l -f /etc/ssh/ssh_host_ed25519_key.pub
```

Then open Terminal or PowerShell on your regular computer and run `ssh YOUR_UBUNTU_USER@YOUR_VM_IP`, replacing both names with your Step 4 details. On the first connection, compare the displayed fingerprint with the one above before accepting it. Enter your Ubuntu password. You can now paste the **Ubuntu Terminal** commands there; leave this connection open during installation. When Ubuntu shuts down in Step 11, the connection will close. Reconnect after Step 12.

### Step 5: get the small tools used below

**Where: Ubuntu Terminal.**

```sh
sudo apt update && sudo apt install --yes ca-certificates curl python3 openssl
```

Wait until the command finishes and you see the command prompt again. If it reports an error, fix that error before continuing.

### Step 6: download the installer

**Where: Ubuntu Terminal.** Create a folder:

```sh
mkdir "$HOME/droidmate-rc17" && cd "$HOME/droidmate-rc17"
```

If it says the folder already exists, open it with `cd "$HOME/droidmate-rc17"` and check what is already there. If it contains files from an earlier installation attempt, stop and check that attempt before starting another one.

Download the installer:

```sh
curl --fail --location --output droidmate-rc17.pyz https://github.com/chilman408/DroidDeck/releases/download/v0.1.1-rc.17/droidmate-rc17.pyz
```

The file is about **37 MB**. A `.pyz` file is a Python program packaged into one file. You do not need to unzip it.

### Step 7: check that the download is correct

**Where: Ubuntu Terminal, still in the same folder.**

Copy this entire box exactly:

```sh
printf '%s  %s\n' 'af5e342b907cc9e906e6698284b70ae051495b51706ba35803568cc889708d37' 'droidmate-rc17.pyz' | sha256sum --check -
```

You should see:

```text
droidmate-rc17.pyz: OK
```

This checks the file's fingerprint against the [published RC17 release](https://github.com/chilman408/DroidDeck/releases/tag/v0.1.1-rc.17). If you see `FAILED`, do not run the installer.

Now check the installer's built-in signature:

```sh
python3 -I -B droidmate-rc17.pyz verify
```

You should see a report containing `droidmate-aggregate-installer-verified` and `RC17`, without an error. The extra Python options are part of the command; leave them in place.

### Step 8: ask the installer what it found

**Where: Ubuntu Terminal.**

```sh
sudo python3 -I -B droidmate-rc17.pyz plan
```

This saves verified installer files and reports the type of installation. It does not start or stop DroidMate services.

Look for `layout` in the result:

| Result | Meaning |
| --- | --- |
| `clean` | No supported DroidMate installation was found. Use the new-install command in Step 13. |
| `portable` or `recognized-legacy` | A supported existing installation was found. Use the update command in Step 13. |
| An error or refusal | Stop. Keep the message and the existing files. Do not erase records to get past it. |

On a new VM, `readyForInstallation: false` is expected at this stage. On an existing installation, `readyForPublication: false` is a frozen internal planning field; it is not a statement that the public RC17 release is unavailable.

## Part 3: prepare shared GPU encoding

**This part uses the Unraid Terminal, not Ubuntu.** It gives this one VM access to the qualified GPU encoding addon. VirtIO 3D and VNC stay available. Other VMs and containers keep their existing setup.

**If this VM already has a working DroidMate GPU addon, keep its existing addon and recovery notes. Skip to Step 13.** Do not build a replacement profile over it. If you are unsure, stop and ask for help before changing the VM.

### Step 9: download and check the GPU addon

Leave Ubuntu running. On your regular computer, return to the Unraid web page and open its **Terminal** icon.

**Where: Unraid Terminal.**

```sh
work=$(mktemp -d /tmp/droidmate-rc17.XXXXXX) && cd "$work" &&
  curl --fail --location --output droidmate-gpu-host-v1.tar https://github.com/chilman408/DroidDeck/releases/download/v0.1.1-rc.17/droidmate-gpu-host-v1.tar &&
  printf '%s  %s\n' 'bfd4757afe37f0b677ad9c590500566606967413bfecc0d8901e46bbfdafe5a2' 'droidmate-gpu-host-v1.tar' | sha256sum --check -
```

You should see `droidmate-gpu-host-v1.tar: OK`. Do not continue after a failed download or check.

### Step 10: prepare this VM's private addon

**Where: the same Unraid Terminal.**

In the first line below, replace `YOUR_VM_NAME` with the exact VM name from Unraid. Keep the quotation marks. Copy the whole box after making that change:

```sh
vm_name='YOUR_VM_NAME'
vm_uuid=$(virsh domuuid "$vm_name") &&
  generation=$(date -u +%Y%m%d%H%M%S) &&
  addon="/etc/libvirt/droidmate-gpu-addon-rc17-$generation" &&
  profile="/etc/libvirt/droidmate-qemu-launcher-profile-rc17-$generation" &&
  manifest_sha=d9f2eda96d5477316bbf8425889b4eb9160afd916f5afe1642c75f7185bf7c96 &&
  test ! -e "$addon" && test ! -e "$profile" &&
  mkdir -m 700 "$addon" &&
  tar -xf droidmate-gpu-host-v1.tar -C "$addon" &&
  php "$addon/source/tools/qemu-video-addon-prepare.php" "$addon" "$manifest_sha" "$vm_uuid" "$profile" plan &&
  php "$addon/source/tools/qemu-video-addon-prepare.php" "$addon" "$manifest_sha" "$vm_uuid" "$profile" prepare
```

The VM must still be **running**, with its original Unraid graphics setup, during this step.

You should see `prepared: true` (shown as JSON, with quotation marks around the field name). The result includes **`manifestSha256`**. Copy that long value into a private note. This is the fingerprint of the new profile for your VM. It is different from the download fingerprint above.

If the command fails, stop. Do not rerun preparation into the same directory or change the host to bypass its checks.

### Step 11: shut down only this VM

**Where: Unraid's VMs page.**

1. Choose **Shut Down** for the VM you are installing DroidMate on. Do not choose Force Stop.
2. Wait until it is fully stopped.
3. Leave the Unraid Terminal open. Its variables are needed in the next step.

Do not shut down the whole Unraid server or any other VM.

### Step 12: attach the profile and start the VM

**Where: the same Unraid Terminal.**

Replace `PASTE_PROFILE_HASH_HERE` with the **`manifestSha256` value from Step 10**, keeping the quotation marks:

```sh
profile_sha='PASTE_PROFILE_HASH_HERE'
virsh domstate "$vm_uuid"
```

It must say **`shut off`**. Then run:

```sh
php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" plan &&
  php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" install &&
  php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" status &&
  virsh start "$vm_uuid"
```

Look for `phase: installed` and a message that the VM started. Reopen its VNC console and sign in to Ubuntu.

Before closing the Unraid Terminal, print your recovery details:

```sh
printf 'VM_UUID=%s\nADDON=%s\nPROFILE=%s\nPROFILE_SHA256=%s\n' "$vm_uuid" "$addon" "$profile" "$profile_sha"
```

Save these four values privately. You will need them for host Undo. Do not post them, VM XML or passwords in public issue reports.

## Part 4: install DroidMate inside Ubuntu

### Step 13: run ONE installation command

**Where: Ubuntu Terminal.** Return to the download folder:

```sh
cd "$HOME/droidmate-rc17"
```

Choose **only one** of the following two boxes.

**A. Brand-new Ubuntu VM**

Replace `YOUR_VM_IP` with the VM's IPv4 address from Step 4. `whoami` fills in your current Ubuntu username automatically.

```sh
sudo python3 -I -B droidmate-rc17.pyz install-new "$(whoami)" YOUR_VM_IP
```

For example, if your VM really uses `192.168.1.50`, the command ends with `"$(whoami)" 192.168.1.50`.

**B. Existing supported DroidMate/DroidDeck installation**

```sh
sudo python3 -I -B droidmate-rc17.pyz install-existing
```

This update preserves your Android apps, accounts, lockscreen choice, browser settings, Ubuntu desktop and TLS identity.

**While it runs:** keep the VM powered on and the terminal open. A fresh installation downloads Android and other components from their publishers, builds software and starts the services. Allow at least half an hour, and longer if downloads are slow. An update can be much shorter. Do not launch another copy because it seems quiet.

At the end, look for **`phase: accepted`** in the final report and no error. Seeing the command prompt again by itself does not mean installation succeeded.

If it fails or stops unexpectedly, go to [If something goes wrong](#if-something-goes-wrong). Do not start the full installation again.

### Step 14: open DroidMate

On a computer connected to the **same home network**, open this address in your browser, replacing `YOUR_VM_IP` with the real address:

```text
https://YOUR_VM_IP:8443/
```

Use **https**, include **:8443**, and use the **Ubuntu VM's address**, not Unraid's address.

A new installation creates its own local HTTPS certificate. Your browser may warn that it does not know that certificate. Before trusting it, confirm you reached your own VM. On a new installation, you can display its public certificate fingerprint in **Ubuntu Terminal**:

```sh
openssl x509 -in "$HOME/.config/droiddeck/cert.pem" -noout -sha256 -fingerprint
```

Compare it with the SHA-256 fingerprint shown in the browser's certificate details. Trust it only if they match. Never copy or upload `key.pem`, which is the private key. If your browser cannot accept the local certificate, use a properly trusted HTTPS reverse proxy rather than turning certificate checks off.

If you already use a working HTTPS hostname or reverse proxy for DroidMate, keep using it. An existing-install update keeps the existing TLS identity.

**You should now see Android.** On a new installation, complete Android's first-run setup. Aurora Store is included. App and account sign-ins are yours to complete; the installer does not copy anyone else's accounts.

### Step 15: try it, then check a reboot

1. Open an app. Check that the picture appears and touch or mouse clicks work.
2. Start some audio. Click DroidMate's **Unmute** control and check the sound.
3. Open **Settings**. Try the display and audio controls.
4. Refresh the page. The picture and sound should return.
5. Restart Ubuntu normally, using its power menu. Wait for it to boot, then reopen DroidMate. Android should return without running the installer again.

Keep the `droidmate-rc17` download folder and your private GPU recovery notes. **You are done.**

### Using it in a Tesla

Test while parked. Use a hostname the car can actually reach, with a certificate it trusts. A home-network IP address normally will not work while the car is on cellular.

This installer does not set up internet access, DNS or your reverse proxy. Optional browser login defaults to off, so keep a new installation on a trusted LAN until you have configured authentication and protected remote access. Do not simply forward port 8443 to the internet.

## If something goes wrong

**First: stop and keep the error message.** Do not delete installation records, private profiles or Android data. Do not paste passwords, keys or account tokens into an issue report.

| What happened? | What to do |
| --- | --- |
| Download fingerprint says `FAILED` | Do not execute the file. Check the release link and download. |
| `verify` or `plan` refuses the installation | Keep the message and ask for help. Do not bypass the check. |
| GPU preparation refuses the host | Stop the host setup. RC17's GPU addon supports a specific tested setup; do not replace global libraries or change GPU permissions. |
| Installation was interrupted | Keep the exact installer and original records. Read the recovery instructions below. |
| The browser cannot connect | Check that the VM is running, check its IP, use `https` and port `8443`, and try from the same LAN first. |
| Android does not return after reboot | Keep the VM and its records intact. Report the failure; do not reinstall over your apps. |

### Recover an interrupted RC17 update

The installer registers automatic recovery for its managed update. If an update was interrupted, let that recovery finish first. Do not run recovery and installation commands at the same time.

When no installation or recovery is still running, the matching installer can recover its own interrupted update:

**Where: Ubuntu Terminal.**

```sh
cd "$HOME/droidmate-rc17" && sudo python3 -I -B droidmate-rc17.pyz recover
```

A failure during the *early new-install foundation* may need its original component records inspected instead. `recover` is not a promise to repair every partially installed Ubuntu/Android foundation. If it refuses, keep the VM and ask for help.

### Undo an accepted RC17 update

**Where: Ubuntu Terminal.**

```sh
cd "$HOME/droidmate-rc17" && sudo python3 -I -B droidmate-rc17.pyz undo
```

This restores the previous guest runtime while preserving Android apps and data. On a new installation, Undo leaves the installed Ubuntu/Android foundation in place; it does not erase the VM or return it to an empty disk.

**If you also want to remove the host GPU profile:** guest Undo must finish successfully first, with CPU capture restored and the GPU owner/lease released. Do not assume this is finished if Undo reports an error.

Then:

1. Shut down only that VM normally in Unraid. Wait until it is stopped.
2. Open **Unraid Terminal**.
3. Fill in the four values from your private Step 12 recovery note:

```sh
vm_uuid='YOUR_SAVED_VM_UUID'
addon='YOUR_SAVED_ADDON_PATH'
profile='YOUR_SAVED_PROFILE_PATH'
profile_sha='YOUR_SAVED_PROFILE_SHA256'
virsh domstate "$vm_uuid"
```

4. Confirm it says **`shut off`**, then run:

```sh
php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" undo &&
  virsh start "$vm_uuid"
```

This restores that VM's original emulator setting. Do not delete the profile directories or try to reuse an undone profile for another installation.

## A few things that may look confusing

- **DroidDeck in a URL, filename or service name:** expected. The app is now DroidMate; older internal names remain for compatibility.
- **Core version `0.1.1-rc.20`:** expected inside RC17. It is an included component's version, not a newer installer you need to find.
- **PIN or lockscreen:** disabled by default on a new install. An existing PIN is preserved, and you can use a lockscreen for private apps.
- **GPU and CPU:** RC17 prefers qualified GPU encoding and keeps CPU fallback. It does not promise a particular whole-server CPU saving. GPU resize is used for supported same-aspect downscaling; other preprocessing can remain on CPU.
- **Ubuntu updates:** new installs retain the qualified Waydroid 1.6.2 compatibility pin. Other Ubuntu package/security updates remain enabled.
- **A certificate problem on an older preview:** the included legacy renewal helper handles its owned expiring self-signed certificate. Automatic renewal of portable-install certificates is not yet qualified; use the appropriate maintenance path rather than editing installation records.

This is the current, readable walkthrough. The `INSTALL.md` attached to the original RC17 release remains the original publication snapshot, so its checksum and the signed installer stay unchanged. For exact testing scope, see [the qualification record](QUALIFICATION.json). Developers can read the frozen [GPU component reference](docs/GPU-ADDON.md); its older candidate wording records the build stage and does not replace this release guide.
