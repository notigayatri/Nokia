from flask import Flask, jsonify, request

app = Flask(__name__)

users = {
    1: {"id": 1, "name": "Alice"},
    2: {"id": 2, "name": "Bob"}
}

@app.route("/users", methods=["GET"])
def get_users():
    return jsonify(list(users.values()))

@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    user = users.get(user_id)
    if user:
        return jsonify(user)
    else:
        return jsonify({"error": "User not found"}), 404

@app.route("/users", methods=["POST"])
def add_user():
    data = request.get_json()
    new_id = max(users.keys()) + 1
    user = {"id": new_id, "name": data.get("name")}
    users[new_id] = user
    return jsonify(user), 201

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "UP"}), 200


if __name__ == "__main__":
    app.run(debug=True)
