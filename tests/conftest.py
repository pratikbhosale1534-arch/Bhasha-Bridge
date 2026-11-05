import os, sys, tempfile
os.environ.pop("WATSONX_API_KEY", None); os.environ.pop("WATSONX_PROJECT_ID", None)
os.environ["APP_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
