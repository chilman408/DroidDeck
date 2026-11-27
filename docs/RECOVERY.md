# Recover DroidMate or undo an update

Keep the same Ubuntu VM, disk, and installer you used. The installer saved
the files it needs for recovery on that VM.

1. If installation or recovery is still running, leave it open and let it finish.
2. Pick the guide for the version you were installing or updating to.
3. Follow **Recover an interrupted install or update** if it stopped partway
   through. Follow **Undo an update** if it finished and you want to go back.

- **DroidMate 0.1.5** | [Recover](../INSTALL.md#recover-an-interrupted-install-or-update) | [Undo](../INSTALL.md#undo-an-update)
- **DroidMate 0.1.3** | [Recover](https://github.com/chilman408/DroidMate/releases/download/v0.1.3/INSTALL.md#recover-an-interrupted-install-or-update) | [Undo](https://github.com/chilman408/DroidMate/releases/download/v0.1.3/INSTALL.md#undo-an-update)
- **DroidMate 0.1.2** | [Recover](https://github.com/chilman408/DroidMate/releases/download/v0.1.2/INSTALL.md#recover-an-interrupted-install-or-update) | [Undo](https://github.com/chilman408/DroidMate/releases/download/v0.1.2/INSTALL.md#undo-an-update)
- **DroidMate 0.1.1** | [0.1.1 recovery and Undo](https://github.com/chilman408/DroidMate/blob/42a2bd6958629264bed767fbd86fdedba2e7e276/docs/RECOVERY.md)

If the command stops with an error, keep the VM and installer. Save the error
message for troubleshooting.

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
