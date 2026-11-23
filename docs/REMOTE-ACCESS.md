# Open DroidMate from outside your home network

First, get DroidMate working at home. Then set up one **reverse proxy**. It gives your browser a secure address and sends its requests to your DroidMate VM.

The examples use `droidmate.example.com` for your public name and `192.168.1.50` for your Ubuntu VM. Replace both with your own values.

## 1. Get DroidMate ready

1. Open DroidMate on your home network. Go to **Settings → Connectivity**. Turn on **Browser Login**, choose a username and a strong password of at least 12 characters, and click **Save**. Open the local address in a private browser window and check that it asks you to sign in.
2. In **Connectivity**, enter your public name in **Hostname** and click **Apply**. Enter just the name, such as `droidmate.example.com`, without `https://`.
3. In your domain provider’s DNS settings, create an **A record** that points your name to your router’s public IPv4 address. If your public address changes, a dynamic DNS (DDNS) name can keep it up to date. Use the exact name you entered in DroidMate.

Pick **one** of the options below. When the proxy is ready, forward outside **TCP 443** to it in your router’s settings. Keep the Ubuntu port **8443** and your admin pages on your home network. The certificate steps below explain when the proxy also needs **TCP 80**.

## 2A. Use a Synology NAS

1. In DSM, open **Control Panel → Login Portal → Advanced → Reverse Proxy → Create**. Give the rule a name such as `DroidMate`.
2. Set the **Source** to **HTTPS**, your public hostname, and port **443**. Set the **Destination** to **HTTPS**, your Ubuntu VM’s address, and port **8443**.
3. Open **Custom Header → Create → WebSocket**. This lets the video, sound and touch connection through. Under **Advanced Settings**, use **HTTP/1.1** for the proxy connection if you see the option. Save the rule. [Synology reverse proxy help](https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/system_login_portal_advanced)
4. Open **Control Panel → Security → Certificate**. Add a **Let’s Encrypt** certificate for your public name, or use an existing certificate that covers it. Under **Settings**, assign that certificate to the DroidMate reverse proxy rule. For your own domain, DSM normally needs port **80** forwarded to the NAS for certificate checks and renewals. Synology DDNS can validate through DNS. [Synology certificate walkthrough](https://kb.synology.com/en-us/DSM/tutorial/How_to_enable_HTTPS_and_create_a_certificate_signing_request_on_your_Synology_NAS)

Go to Step 3 below.

## 2B. Use Nginx on the Ubuntu VM

This example runs Nginx on the same Ubuntu VM as DroidMate. The connection between Nginx and DroidMate’s local HTTPS port stays on that machine.

1. In **Ubuntu Terminal**, install Nginx and Certbot. Certbot gets the certificate your browser trusts.

   ```sh
   sudo apt update && sudo apt install --yes nginx certbot python3-certbot-nginx
   ```

2. Open a new site file with `sudo nano /etc/nginx/sites-available/droidmate`. Paste this starter file and replace the example name with yours. Save with **Ctrl+O**, **Enter**, then **Ctrl+X** to exit.

   ```nginx
   server {
       listen 80;
       server_name droidmate.example.com;

       location / {
           return 404;
       }
   }
   ```

3. Turn on that site. If Ubuntu’s firewall is enabled, allow **Nginx Full**. On your router, forward **TCP 80** to this same Ubuntu VM so Certbot can check the name now and on renewal.

   ```sh
   sudo ln -s /etc/nginx/sites-available/droidmate /etc/nginx/sites-enabled/droidmate
   sudo nginx -t && sudo systemctl reload nginx
   ```

4. Get the certificate. Replace the example name and follow Certbot’s prompts. It adds HTTPS and makes ordinary HTTP requests go to HTTPS. [Certbot Nginx instructions](https://certbot.eff.org/instructions?ws=nginx&os=snap)

   ```sh
   sudo certbot --nginx --redirect -d droidmate.example.com
   ```

5. Open the site file again. In the server block listening on **443 with SSL**, replace just the `location /` block with this one. Use your own Ubuntu address in `proxy_pass`. Keep the certificate lines Certbot added. [Nginx WebSocket instructions](https://nginx.org/en/docs/http/websocket.html)

   ```nginx
   location / {
       proxy_pass https://192.168.1.50:8443;
       proxy_http_version 1.1;
       proxy_set_header Host $host;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_read_timeout 3600s;
       proxy_buffering off;
   }
   ```

6. Save the file, then check and reload Nginx. If the check reports an error, fix the file before you try to reload.

   ```sh
   sudo nginx -t && sudo systemctl reload nginx
   ```

## 3. Try it from outside

Turn Wi-Fi off on your phone and visit your public **HTTPS** address, for example `https://droidmate.example.com/`. You should get a login prompt and see Android after signing in. Tap **Unmute** and try a touch or click. When that works, try the same address in the Tesla browser while parked.

- **Certificate warning?** Check that the proxy serves the certificate matching your public name before signing in.
- **Page opens but video or input doesn't work?** Check that WebSocket forwarding is enabled. Check that the proxy passes your public hostname as the `Host` header and that it matches **Hostname** in DroidMate.
- **Nothing opens?** Try the local DroidMate address first. If that works, check the public DNS name, your router’s port forwarding and the proxy’s destination. If you’re taking the public link down, remove its router port-forwarding rule.

[Back to installation](../INSTALL.md)
