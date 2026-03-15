# Firebase Firestore + Hosting Setup

This project runs a Flask backend (camera + QR + admin APIs).  
Firebase Hosting can be used as the public web entry point, but the Flask app must run on Cloud Run (or another server).

## 1) Create Firebase resources

1. Create/select a Firebase project in Firebase Console.
2. Enable **Firestore Database** in **Native mode**.
3. Create a service account key JSON from Google Cloud Console:
   - IAM & Admin -> Service Accounts -> Keys -> Add key -> JSON.
4. Save the JSON file securely (do not commit it).

## 2) Configure environment variables

Set these variables before starting Flask:

```powershell
$env:FIRESTORE_SYNC_ENABLED="1"
$env:FIREBASE_PROJECT_ID="your-firebase-project-id"
$env:FIREBASE_SERVICE_ACCOUNT_PATH="C:\path\to\service-account.json"
```

Alternative to file path:
- Set `FIREBASE_SERVICE_ACCOUNT_JSON` with full JSON string.
- Or set `GOOGLE_APPLICATION_CREDENTIALS` to key path.

## 3) Install backend dependencies

```powershell
python -m pip install -r requirements-flask.txt
python -m pip install -r requirements-firestore.txt
```

## 4) Verify Firestore connection

Start app:

```powershell
python app.py
```

Check:
- `GET /api/status`
- `GET /api/scanner/state`
- `GET /api/dashboard` (admin session)

You should see:

```json
"firestore": {
  "enabled": true,
  "connected": true
}
```

## 5) Deploy backend to Cloud Run

Use your preferred Dockerfile/source deploy. Example source deploy:

```powershell
gcloud run deploy bus-depot-api `
  --source . `
  --region asia-south1 `
  --allow-unauthenticated `
  --set-env-vars FIRESTORE_SYNC_ENABLED=1,FIREBASE_PROJECT_ID=your-firebase-project-id `
  --set-env-vars FIREBASE_SERVICE_ACCOUNT_PATH=/secrets/service-account.json
```

Note:
- For production, mount secret via Secret Manager instead of plain env path.

## 6) Configure Firebase Hosting

Files added:
- `firebase.json` (hosting rewrite to Cloud Run service `bus-depot-api`)
- `.firebaserc` (set your project id)

Update `.firebaserc`:

```json
{
  "projects": {
    "default": "your-firebase-project-id"
  }
}
```

Deploy hosting:

```powershell
firebase login
firebase use your-firebase-project-id
firebase deploy --only hosting
```

Now traffic on Firebase Hosting URL routes to Cloud Run, and Firestore sync is active.
