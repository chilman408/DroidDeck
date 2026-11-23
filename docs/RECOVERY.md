# Recover or undo DroidMate 0.1.1

Start here if the 0.1.1 installer stopped partway through or you want to undo a completed update. Keep the same Ubuntu VM, disk and installer download. These steps use the files the installer saved on that VM.

## Recover an interrupted install or update

1. If the installer is still running, leave it open and let it finish. An interrupted update can recover on its own. Give it time, then try your usual DroidMate address again.
2. If DroidMate still won’t open and the installer or automatic recovery has finished, open **Terminal in Ubuntu** with **Ctrl+Alt+T**. Pick the box that matches what you were doing.

   **You were updating from public RC17.**

   ```sh
   cd "$HOME/droidmate-0.1.1" && sudo python3 -I -B droidmate-0.1.1-install.pyz upgrade-recover
   ```

   **You were installing 0.1.1 on fresh Ubuntu.**

   ```sh
   cd "$HOME/droidmate-0.1.1" && sudo python3 -I -B droidmate-0.1.1-install.pyz recover
   ```

3. Wait for the result. **`"phase": "accepted"`** means the install or update had already finished. **`"phase": "restored"`** means DroidMate put the earlier streaming setup back. Open your usual DroidMate address and try an app and its sound.

If you stepped away during the first part of a fresh install, the installer may not have saved enough information to recover yet. If you see an error about missing install history, or DroidMate still won’t open, keep the files and share the error when asking for help. Leave passwords and keys out of the report.

## Undo an update

Use this if you updated from **public RC17 to 0.1.1**, the update finished, and you want to go back to the earlier streaming setup. Undo restores CPU streaming and keeps your Android apps and data. The browser may show the earlier settings layout.

1. Open **Terminal in Ubuntu**. Paste this command and press **Enter**.

   ```sh
   cd "$HOME/droidmate-0.1.1" && sudo python3 -I -B droidmate-0.1.1-install.pyz upgrade-undo
   ```

2. Wait for **`"phase": "restored"`**. Reopen DroidMate and check that Android opens and sound plays. If the command reports an error, save it before asking for help.

### Undo a fresh 0.1.1 install

On a fresh installation that already finished, use the command below instead. This restores the CPU streaming setup and keeps Ubuntu, Android and your apps on the disk.

```sh
cd "$HOME/droidmate-0.1.1" && sudo python3 -I -B droidmate-0.1.1-install.pyz undo
```

Wait for **`"phase": "restored"`**, then check the picture and sound in your browser.

## Remove the Unraid GPU addon

This part is only for a VM where you set up the [Unraid GPU addon](UNRAID-GPU.md).

1. Finish the matching Undo steps above **in Ubuntu first**. Wait for **`"phase": "restored"`** and check that video plays. If Undo reports an error, stop here.
2. On Unraid’s **VMs** page, shut down **only the Ubuntu VM**. Open **Unraid Terminal** from the Unraid web page.
3. Find the four lines you saved when you [installed the addon](UNRAID-GPU.md#5-save-your-undo-details-and-finish). Put the value from each saved line in place of the matching `YOUR_SAVED_…` text below. Keep the quotes, then paste the box into Unraid Terminal.

   ```sh
   vm_uuid='YOUR_SAVED_VM_UUID'
   addon='YOUR_SAVED_ADDON_PATH'
   profile='YOUR_SAVED_PROFILE_PATH'
   profile_sha='YOUR_SAVED_PROFILE_SHA256'
   virsh domstate "$vm_uuid"
   ```

4. Once you see **`shut off`**, run this box in the same Unraid Terminal.

   ```sh
   php "$addon/source/tools/qemu-video-launcher-install.php" "$profile" "$profile_sha" undo &&
     virsh start "$vm_uuid"
   ```

5. Look for **`"phase": "undone"`** and a message that the VM started. Open DroidMate again and check the picture and sound. Keep the saved notes and profile folders in case you need help later.

[Back to installation](../INSTALL.md)
