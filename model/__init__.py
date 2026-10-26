# This directory holds trained model artifacts.
#
# Run `python train_model.py` to populate it with, per climate zone
# (desert / temperate / tropical / continental):
#   model/<zone>/point_model.joblib
#   model/<zone>/lower_model.joblib
#   model/<zone>/upper_model.joblib
#   model/<zone>/scaler.joblib
#   model/<zone>/meta.json
#
# If these files are missing when app.py starts, the app automatically
# trains a fast fallback linear model on synthetic data in-memory so the
# app is always runnable out of the box (see app.py -> ensure_models()).
