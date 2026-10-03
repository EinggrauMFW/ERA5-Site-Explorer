import json
import re
from flask import jsonify, abort

from devices_catalogue import load_catalogue, find_device, summary_dict, detail_dict
from device import power_matrix_from_arrays
from plugins import _json_default

def register(app, ctx):
    @app.route("/api/devices", methods=["GET"])
    def list_devices_catalogue():
        catalogue = load_catalogue()

        devices_list = []
        for dev in catalogue.devices:
            devices_list.append(summary_dict(dev))

        return jsonify({
            "configured": catalogue.configured,
            "directory": catalogue.directory,
            "devices": devices_list,
            "warnings": catalogue.warnings
        })

    @app.route("/api/devices/<device_id>", methods=["GET"])
    def get_device_catalogue(device_id):
        if not re.match(r'^[a-z0-9_-]{1,60}$', device_id):
            abort(404)

        dev = find_device(device_id)
        if dev:
            matrix = power_matrix_from_arrays(dev.hs_m, dev.period_s, dev.power_kw, dev.bin_convention)
            detail = detail_dict(dev, matrix.hm0_edges.tolist(), matrix.period_edges.tolist())
            return app.response_class(json.dumps(detail, default=_json_default), mimetype="application/json")

        abort(404)
