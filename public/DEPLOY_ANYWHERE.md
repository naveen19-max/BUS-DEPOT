# Deploy For Public Access (Use Anywhere)

This app can be public, but there is one important architecture rule:

- `SERVER_CAMERA_ENABLED=1` uses webcam attached to the server machine.
- For cloud/public hosting, set `SERVER_CAMERA_ENABLED=0` and use **Device Camera Scan** from browser on `/scanner`.

## Option A: Render (Fastest)

1. Push this folder to GitHub.
2. In Render, create **New Web Service** from that GitHub repo.
3. Render detects `render.yaml`.
4. Set environment variables in Render:
   - `MYSQL_HOST`
   - `MYSQL_PORT` = `3306`
   - `MYSQL_USER`
   - `MYSQL_PASSWORD`
   - `MYSQL_DATABASE` = `bus_depot`
   - `FLASK_HOST` = `0.0.0.0`
   - `SERVER_CAMERA_ENABLED` = `0`
   - Optional Firestore:
     - `FIRESTORE_SYNC_ENABLED` = `1`
     - `FIREBASE_PROJECT_ID`
     - `FIREBASE_SERVICE_ACCOUNT_JSON` (full JSON string)
5. Deploy. Render gives URL like:
   - `https://bus-depot-api.onrender.com`

## Option B: Cloud Run + Firebase Hosting

Use [FIREBASE_SETUP.md](./FIREBASE_SETUP.md).  
Flow:

1. Deploy Flask backend to Cloud Run.
2. Configure `firebase.json` rewrite to Cloud Run service.
3. `firebase deploy --only hosting`.

Public URL:
- `https://<project-id>.web.app`

## Verify after deploy

1. Open `/scanner`.
2. Click **Start Device Camera Scan**.
3. Scan driver QR once and register details.
4. Scan same QR again -> toggles entry/exit.
5. Scan admin QR -> auto open dashboard/report.
