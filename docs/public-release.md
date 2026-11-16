# Google studio release

This source snapshot includes Google sign-in, account-owned libraries, guarded shared generation and the music-first customer UI. It is published as a separate release branch; main is unchanged.

The deployed tested profile generates 30-second MP3s. Longer durations are not yet validated. No model weights, audio, credentials, account data or private execution records are included.

Deployment-specific identity values are supplied through SONORA_GOOGLE_CLIENT_ID, SONORA_OWNER_EMAIL, SONORA_ARCHIVE_GUARD, SONORA_ARCHIVE_RUN_ID and SONORA_ARCHIVE_JOB_ID. Archive settings preserve an existing deployment's track history. Configure a real privacy contact before deployment. Resource names, image/checkpoint pins and budget constants in the example adapters must be reviewed for your own deployment. Do not deploy this sanitized snapshot over a running studio without restoring its deployment configuration.

Local tests: PYTHONPATH=backend python -m unittest discover -s tests. Frontend: cd frontend && npm ci && npm test && npm run build.
