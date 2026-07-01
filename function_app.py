"""
function_app.py
SiteDocs REST API — Azure Functions v2

Route blueprints are registered from the routes/ and scheduled/ packages.
See each module for endpoint documentation.
"""

import azure.functions as func

from routes.entities import bp as entities_bp
from routes.forms import bp as forms_bp
from routes.proxies import bp as proxies_bp
from routes.etl import bp as etl_bp
from routes.rates import bp as rates_bp
from routes.health import bp as health_bp
from scheduled.timers import bp as timers_bp

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

app.register_functions(entities_bp)
app.register_functions(forms_bp)
app.register_functions(proxies_bp)
app.register_functions(etl_bp)
app.register_functions(rates_bp)
app.register_functions(health_bp)
app.register_functions(timers_bp)
