# Turn on shared GPU encoding in Unraid

This is optional. The addon lets one Ubuntu VM use a shared AMD GPU to compress video. Your **VNC console** stays available. The tested setup uses **Unraid 7.1.4, QEMU 9.2.3, and VirtIO(3D)**. The addon checks the actual host setup before it makes changes.

Use this with a **new Ubuntu VM** after [Step 1 of the install guide](../INSTALL.md#1-set-up-ubuntu). If this VM already has a working DroidMate GPU addon, keep that setup. Keep its Undo notes handy.

## 1. Download the addon

Keep the **Ubuntu VM running**. In your browser, open the **Unraid web page** and click its **Terminal** icon. Copy this box into **Unraid Terminal**.

```sh
work=$(mktemp -d /tmp/droidmate-0.1.1.XXXXXX) && cd "$work" &&
  curl --fail --location --output droidmate-gpu-host-v2.tar https://github.com/chilman408/DroidMate/releases/download/v0.1.1/droidmate-gpu-host-v2.tar &&
  printf '%s  %s\n' '0e8ad94c23fb704439eeac7a72ea0970a896b8fa5222c9bbf7b647092199f06f' 'droidmate-gpu-host-v2.tar' | sha256sum --check -
```

Wait for **`droidmate-gpu-host-v2.tar: OK`**. If the check fails, stop here.

## 2. Get the addon ready for your VM

In Unraid’s **VMs** page, find the exact name of your Ubuntu VM. Replace `YOUR_VM_NAME` in the first line below with that name, keeping the quotes. For example, the line could say `vm_name='DroidMate'`. Run the whole box in the **same Unraid Terminal**. Keep Ubuntu running for this step.

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

Look for **`"prepared": true`**. Find **`manifestSha256`** in that result and copy its long value into a private note. You’ll paste it in Step 4. If preparation fails, keep the error and stop. The VM can still use the regular [installation guide](../INSTALL.md) with CPU encoding if its 3D graphics are working.

## 3. Shut down just the Ubuntu VM

On Unraid’s **VMs** page, choose **Shut Down** for your Ubuntu VM and wait for it to stop. Leave the Unraid server and that Terminal window running.

## 4. Attach the addon and start Ubuntu

In the same Unraid Terminal, replace `PASTE_PROFILE_HASH_HERE` with the long value you saved in Step 2. Run this box.

```sh
profile_sha='PASTE_PROFILE_HASH_HERE'
virsh domstate "$vm_uuid"
```

Once you see **`shut off`**, run the next box.

```sh
php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" plan &&
  php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" install &&
  php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" status &&
  virsh start "$vm_uuid"
```

Look for **`"phase": "installed"`** and a message that the VM started. Open its **VNC console** and sign in to Ubuntu.

## 5. Save your Undo details and finish

Back in **Unraid Terminal**, run this and save the four lines it prints in a private note.

```sh
printf 'VM_UUID=%s\nADDON=%s\nPROFILE=%s\nPROFILE_SHA256=%s\n' "$vm_uuid" "$addon" "$profile" "$profile_sha"
```

Continue from [Step 2 of the DroidMate install guide](../INSTALL.md#2-write-down-ubuntus-address). Once Android is playing in your browser, open **Settings → Display** and look for **Encoding: GPU**.

To take the addon off later, use the four lines you saved. [Remove the Unraid GPU addon](RECOVERY.md#remove-the-unraid-gpu-addon)
