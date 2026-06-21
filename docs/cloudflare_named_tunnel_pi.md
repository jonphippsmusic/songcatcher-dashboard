# Named Cloudflare Tunnel for the Song Catcher API

The current `trycloudflare.com` Quick Tunnel is suitable for testing only. For deployment, use a named Cloudflare Tunnel routed to a stable hostname such as:

```text
api.yourdomain.com
```

## 1. Confirm the API origin

On the machine running the FastAPI app:

```bash
curl http://localhost:8000/api/v1/stations
```

Adjust the port if your API uses a different one.

## 2. Create and route the tunnel

```bash
cloudflared tunnel login
cloudflared tunnel create songcatcher-api
cloudflared tunnel route dns songcatcher-api api.yourdomain.com
```

## 3. Create config

```bash
sudo mkdir -p /etc/cloudflared
sudo nano /etc/cloudflared/config.yml
```

Example:

```yaml
tunnel: songcatcher-api
credentials-file: /home/YOUR-USER/.cloudflared/YOUR-TUNNEL-UUID.json

ingress:
  - hostname: api.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

## 4. Run as service

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared --no-pager -l
```

## 5. Test public endpoint

```bash
curl https://api.yourdomain.com/api/v1/stations
```

## 6. Update the Pi uploader env files

On the BirdNET Pi:

```bash
sudo sed -i.bak \
's#https://[^[:space:]]*\.trycloudflare\.com#https://api.yourdomain.com#g' \
/etc/songcatcher-api-uploader.env \
/etc/songcatcher-unfiltered-metadata.env
```

Confirm:

```bash
sudo grep -n "SONGCATCHER_API_URL" \
/etc/songcatcher-api-uploader.env \
/etc/songcatcher-unfiltered-metadata.env
```

Restart uploaders:

```bash
sudo systemctl reset-failed songcatcher-api-uploader.service
sudo systemctl restart songcatcher-unfiltered-metadata-uploader.service
sudo systemctl start songcatcher-api-uploader.service
sudo systemctl start songcatcher-api-uploader.timer
```

Check logs:

```bash
sudo journalctl -u songcatcher-api-uploader.service -n 100 --no-pager -l
```
