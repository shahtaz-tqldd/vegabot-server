from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
FIREBASE_SERVICE_ACCOUNT_FILE = PROJECT_DIR / "secrets" / "firebase-service-account.json"


_firebase_app = None


class FirebaseVerificationError(ValueError):
    pass


def verify_firebase_id_token(id_token):
    print(id_token)
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
    except ImportError as exc:
        raise FirebaseVerificationError("firebase-admin is required to verify Firebase ID tokens.") from exc

    global _firebase_app
    if _firebase_app is None:
        if firebase_admin._apps:
            _firebase_app = firebase_admin.get_app()
        elif FIREBASE_SERVICE_ACCOUNT_FILE.exists():
            _firebase_app = firebase_admin.initialize_app(
                credentials.Certificate(str(FIREBASE_SERVICE_ACCOUNT_FILE))
            )
        else:
            raise FirebaseVerificationError("Firebase service account credentials are not configured.")

    try:
        return auth.verify_id_token(id_token, app=_firebase_app)
    except Exception as exc:
        raise FirebaseVerificationError("Invalid Firebase ID token.") from exc
