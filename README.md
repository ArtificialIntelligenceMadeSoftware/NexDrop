<p align="center">
	<img src="nexdrop%20logo.png" alt="NEXDROP logo" width="360">
</p>

# NEXDROP

**NEXDROP** is a Windows application for sharing files between a computer and phones on the same trusted Wi-Fi network. The computer hosts the files, and phones connect through a browser using the QR code or address shown by the app.

## Developed By

NEXDROP's application and project development are by **A.I.M.S.**

## Downloads

Download the Windows x64 installer or portable app from the project's Releases:

- **NEXDROP-Setup.exe** installs the program in `C:\Program Files\NEXDROP`. Windows requests administrator approval. The installer creates a writable `NEXDROP_Data` folder for application settings and history.
- **NEXDROP-Portable.exe** runs from its current folder and stores its `NEXDROP_Data` folder beside the executable. Keep the portable app in a folder where your Windows account can write.

The default shared-file location is the current Windows user's **Downloads** folder. Choose another location in Settings with **Choose Folder**.

## Getting Started

1. Install NEXDROP or place the portable executable in a writable folder.
2. Open **Settings**, choose a username and password, and save. The requested default password is `1234`; it is weak, so replace it before sharing files. Use **Show Password** to read the password for mobile sign-in.
3. Choose a shared-file folder if you don't want to use Downloads, then save the settings.
4. Return to **Home** and select **Initialize Server**.
5. Allow NEXDROP through Windows Firewall for local-network access.
6. Connect the phone to the same Wi-Fi network, scan the QR code, and sign in.

## Data and Privacy

Settings and transfer history are stored in `NEXDROP_Data` beside the installed or portable executable. The installer grants standard users permission to write to its data folder under Program Files. Existing settings and transfer history from earlier NEXDROP versions are migrated when possible. Shared files are stored in the folder selected in Settings.

Uninstalling preserves `NEXDROP_Data`. Back it up before removing it if you want to keep settings and transfer history; delete it manually only when you want to remove that data.

User settings, passwords, transfer history, and shared files are not part of the public source files. `NEXDROP_Data`, generated config/history files, and build output are excluded by `.gitignore`.

## Network Safety

NEXDROP is intended for trusted local networks. The default password `1234` is weak; replace it before use. Do not configure router port forwarding or expose the app's HTTP port directly to the public internet. For remote access, use a VPN or a properly secured HTTPS deployment.
