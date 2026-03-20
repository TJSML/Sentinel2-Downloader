from flask import Flask, render_template, request, jsonify
import requests
import os
import threading
download_threads = {}
stop_flags = {}

app = Flask(__name__)

def create_bbox(lat, lon, km=10):
    delta = km / 2 / 111
    return (
        f"POLYGON(("
        f"{lon-delta} {lat-delta}, "
        f"{lon-delta} {lat+delta}, "
        f"{lon+delta} {lat+delta}, "
        f"{lon+delta} {lat-delta}, "
        f"{lon-delta} {lat-delta}"
        f"))"
    )

def get_token(username, password):
    url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    data = {
        "client_id": "cdse-public",
        "username": username,
        "password": password,
        "grant_type": "password",
    }
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]

def search_images(start, end, footprint, max_cloud, level):
    url = (
        "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
        f"?$filter=Collection/Name eq 'SENTINEL-2'"
        f" and OData.CSC.Intersects(area=geography'SRID=4326;{footprint}')"
        f" and ContentDate/Start gt {start}T00:00:00.000Z"
        f" and ContentDate/Start lt {end}T23:59:59.000Z"
        f" and Attributes/OData.CSC.DoubleAttribute/any(att:att/Name eq 'cloudCover'"
        f" and att/OData.CSC.DoubleAttribute/Value lt {float(max_cloud):.2f})"
        f" and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType'"
        f" and att/OData.CSC.StringAttribute/Value eq 'S2MSI{level}')"
        f"&$orderby=ContentDate/Start desc&$top=20&$expand=Attributes"
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.json().get("value", [])

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    try:
        data       = request.json
        username   = data["username"]
        password   = data["password"]
        lat        = float(data["lat"])
        lon        = float(data["lon"])
        km         = float(data["km"])
        start_date = data["start_date"]
        end_date   = data["end_date"]
        max_cloud  = float(data["max_cloud"])
        level      = data["level"]

        token     = get_token(username, password)
        footprint = create_bbox(lat, lon, km)
        items     = search_images(start_date, end_date, footprint, max_cloud, level)

        results = []
        for i in items:
            # ดึงค่า cloud cover จริงจาก Attributes
            cloud = "N/A"
            for attr in i.get("Attributes", []):
                if attr.get("Name") == "cloudCover":
                    cloud = round(float(attr["Value"]), 2)
                    break

            results.append({
                "id":    i["Id"],
                "name":  i["Name"],
                "date":  i["ContentDate"]["Start"][:19].replace("T", " "),
                "cloud": cloud,
                "level": level,
                "size":  f"{km} x {km}",
            })

        return jsonify({"success": True, "token": token, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/download", methods=["POST"])
def download():
    try:
        data         = request.json
        product_id   = data["id"]
        product_name = data["name"]
        token        = data["token"]
        save_dir     = data.get("save_dir", "downloads")

        os.makedirs(save_dir, exist_ok=True)

        filepath = os.path.join(save_dir, f"{product_name}.zip")
        counter = 1
        while os.path.exists(filepath):
            filepath = os.path.join(save_dir, f"{product_name}({counter}).zip")
            counter += 1

        stop_flags[product_id] = False

        url     = f"https://download.dataspace.copernicus.eu/odata/v1/Products({product_id})/$value"
        headers = {"Authorization": f"Bearer {token}"}
        r       = requests.get(url, headers=headers, stream=True, timeout=600)
        r.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if stop_flags.get(product_id):
                    r.close()
                    os.remove(filepath)
                    return jsonify({"success": False, "stopped": True, "message": "Download cancelled"})
                f.write(chunk)

        stop_flags.pop(product_id, None)
        return jsonify({"success": True, "message": f"Downloaded: {os.path.basename(filepath)}", "path": filepath})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/stop", methods=["POST"])
def stop():
    product_id = request.json.get("id")
    stop_flags[product_id] = True
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(debug=True)