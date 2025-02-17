from flask import Flask, request, jsonify
from flask_restful import Api, Resource
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask_caching import Cache  # ✅ Import caching
import threading
import os
from concurrent.futures import ThreadPoolExecutor


# ================================
# 🚀 INITIALIZE FLASK APP & CACHE
# ================================
app = Flask(__name__)
app.config['CACHE_TYPE'] = 'simple'  # ✅ Configure caching
cache = Cache(app)  # ✅ Initialize cache
api = Api(app)

# =======================================
# 🔗 SET UP GOOGLE SHEETS API CONNECTION
# =======================================
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# ✅ Automatically determine credentials file location
if os.getenv("RENDER"):  # ✅ If running on Render, use the secret file in /etc/secrets/
    GOOGLE_CREDENTIALS_PATH = f"/etc/secrets/{os.getenv('GOOGLE_CREDENTIALS_PATH')}"
else:  # ✅ Use the local file path when running locally
    GOOGLE_CREDENTIALS_PATH = "vivid-monitor-451014-a7-0a8a581b3c3a.json"

# ✅ Debugging: Print the file path being used
print(f"[INFO] Using Google Credentials from: {GOOGLE_CREDENTIALS_PATH}")

# ✅ Ensure the file exists before using it
if not os.path.exists(GOOGLE_CREDENTIALS_PATH):
    raise FileNotFoundError(f"Google credentials file not found at {GOOGLE_CREDENTIALS_PATH}")

CREDS = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_PATH, SCOPE)
client = gspread.authorize(CREDS)

# Spreadsheet ID
SPREADSHEET_ID = "1em9fNvDv22xtCnaonn_gB5ThgucD922R9p9svF_ptUs"

# =============================
# 🔑 API KEY VALIDATION
# =============================
# API Keys
API_KEYS = {
    "003fb7e922cd6595f4243703b7d3a32f": "MedSchoolA",
    "2d7ebd0c14a5c41d18172341920cd222": "MedSchoolB",
    "9198340729aae63e06785df4cd61d8b2": "MedSchoolC",
    "20e8b4cad8f774a7bbe076029ba3a38c": "MedSchoolD",
    "a71ed21d7da1aead4e5088827d1c67fc": "MasterKey"
}

# Validate API Key and Student Access
def validate_api_key_and_student(api_key, school_id, student_ids=None):
    if api_key not in API_KEYS:
        return False, "Invalid API key."
    assigned_school = API_KEYS[api_key]
    if assigned_school != "MasterKey" and assigned_school != school_id:
        return False, "Access denied."
    
    if student_ids:
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("roster_data")
        data = sheet.get_all_values()
        valid_students = set(row[1] for row in data[1:] if row[0] == school_id)
        if any(sid not in valid_students for sid in student_ids):
            return False, "One or more students do not belong to the provided school."
    
    return True, None

# =============================
# 📊 EXAM STATISTICS ENDPOINT
# =============================
@cache.cached(timeout=300)  # ✅ Cache for 5 minutes
def get_exam_stats():
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("exam_stats")
    data = sheet.get_all_values()
    stats = [{
        "test_id": row[0],
        "test_name": row[1],
        "n": row[2],
        "min": row[3],
        "max": row[4],
        "median": row[5],
        "mean": row[6],
        "sd": row[7]
    } for row in data[1:] if row[0]]
    return stats

class ExamStats(Resource):
    def get(self):
        return jsonify(get_exam_stats())

# =============================
# 📝 AVAILABLE TESTS ENDPOINT
# =============================
@cache.cached(timeout=300)  # ✅ Cache for 5 minutes
def get_available_tests():
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("exam_stats")
    data = sheet.get_all_values()
    tests = [{
        "test_id": row[0],
        "test_name": row[1]
    } for row in data[1:] if row[0]]
    return tests

class AvailableTests(Resource):
    def get(self):
        return jsonify(get_available_tests())

# =============================================================
# 🎓 STUDENT ROSTER ENDPOINT (Supports Full Fetch & Batch Mode)
# =============================================================
class Students(Resource):
    def get(self):
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        student_ids = request.args.get("student_ids") or request.args.get("student_id")  # Support single & batch requests

        if student_ids:
            student_ids = student_ids.split(",")  # Convert to list

        valid, error_message = validate_api_key_and_student(api_key, school_id, student_ids)
        if not valid:
            return jsonify({"error": error_message})

        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("roster_data")
        data = sheet.get_all_values()
        
        students = [
            {
                "school_name": row[0],
                "student_id": row[1],
                "first_name": row[2],
                "last_name": row[3],
                "campus": row[4] if len(row) > 4 else "N/A",
                "med_year": row[5] if len(row) > 5 else "N/A"
            }
            for row in data[1:]
            if row[0] == school_id and (not student_ids or row[1] in student_ids)
        ]

        return jsonify(students if students else {"error": "No students found."})

# ==============================================
# 📑 FETCH STUDENT SCORES (Supports Batch Mode)
# ==============================================
@cache.cached(timeout=300)
def get_student_scores():
    """ Fetches student scores from multiple sheets and caches results. """
    sheets = ["se_scores", "cas_scores", "nsas_scores"]
    sheets_data = {}
    lock = threading.Lock()  # ✅ Ensures thread-safe access to sheets_data

    def fetch_data(sheet):
        try:
            data = client.open_by_key(SPREADSHEET_ID).worksheet(sheet).get_all_values()
            with lock:  # ✅ Ensure only one thread updates the dictionary at a time
                sheets_data[sheet] = data
        except Exception as e:
            print(f"[ERROR] Failed to fetch data from {sheet}: {e}")

    # ✅ Use ThreadPoolExecutor for efficient multi-threading
    with ThreadPoolExecutor(max_workers=len(sheets)) as executor:
        executor.map(fetch_data, sheets)

    return sheets_data

class StudentScores(Resource):
    def get(self):
        """ Retrieves student scores with optional batch filtering for student_ids and test_ids. """
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")  # No need to normalize since it's already consistent

        # ✅ Accepts both "student_id" and "student_ids" (single or batch requests)
        student_ids = request.args.get("student_ids") or request.args.get("student_id")
        test_ids = request.args.get("test_ids") or request.args.get("test_id")

        # ✅ Convert comma-separated values to lists (ensure they are strings)
        student_ids = [str(sid.strip()) for sid in student_ids.split(",")] if student_ids else []
        test_ids = [str(tid.strip()) for tid in test_ids.split(",")] if test_ids else []

        print(f"[DEBUG] Requested school_id: {school_id}")
        print(f"[DEBUG] Requested student_ids: {student_ids}")
        print(f"[DEBUG] Requested test_ids: {test_ids}")

        # ✅ Validate API key and ensure access to the requested school
        valid, error_message = validate_api_key_and_student(api_key, school_id)
        if not valid:
            print(f"[ERROR] {error_message}")
            return jsonify({"error": error_message})

        # ✅ Fetch cached student scores from all sheets
        sheets_data = get_student_scores()
        print(f"[DEBUG] Retrieved Sheets Data: {sheets_data.keys()}")  # Print sheet names

        scores = []
        for sheet_name, data in sheets_data.items():
            print(f"[DEBUG] Checking sheet: {sheet_name}, Total Rows: {len(data)}")

            for row in data[1:]:  # Skip header row
                dataset_school_id = row[0].strip()
                dataset_student_id = str(row[1]).strip()  # Convert to string
                dataset_test_id = str(row[2]).strip()  # Convert to string

                print(f"[DEBUG] Checking row: School ID={dataset_school_id}, Student ID={dataset_student_id}, Test ID={dataset_test_id}")

                # ✅ Apply filtering conditions ensuring correct school & batch filtering
                if (
                    dataset_school_id == school_id and  # Direct string match
                    (not student_ids or dataset_student_id in student_ids) and
                    (not test_ids or dataset_test_id in test_ids)
                ):
                    scores.append({
                        "school_name": dataset_school_id,
                        "student_id": dataset_student_id,
                        "test_id": dataset_test_id,
                        "test_date": row[3],
                        "score": row[4]
                    })

        print(f"[DEBUG] Final Scores Output: {scores}")
        return jsonify(scores if scores else {"error": "No scores found."})

# =============================================
# 📑 FETCH STUDENT TESTS (Supports Batch Mode)
# =============================================
class StudentTests(Resource):
    def get(self):
        """ Retrieves test records for students, supporting batch requests. """
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        student_ids = request.args.get("student_ids") or request.args.get("student_id")  # ✅ Allow both "student_id" and "student_ids"

        # Convert comma-separated student IDs to a list and ensure they are strings
        student_ids = [str(sid.strip()) for sid in student_ids.split(",")] if student_ids else []

        # Validate API key and access
        valid, error_message = validate_api_key_and_student(api_key, school_id, student_ids)
        if not valid:
            return jsonify({"error": error_message})

        # ✅ Fetch student scores with proper API logic
        scores_data = StudentScores().get().json  # Ensure we get proper JSON response
        if not isinstance(scores_data, list):
            return jsonify({"error": "Failed to fetch student scores."})

        # ✅ Correct filtering logic
        filtered_tests = [
            record for record in scores_data
            if "student_id" in record and str(record["student_id"]) in student_ids
        ]

        return jsonify(filtered_tests if filtered_tests else {"error": "No tests found for the specified students."})

# ====================================================
# 📑 FETCH DETAILED TEST SCORES (Supports Batch Mode)
# ====================================================
@cache.cached(timeout=300)
def get_test_score_details():
    """ Fetches detailed test scores from multiple sheets and caches results. """
    sheets = ["se_scores", "nsas_scores"]
    sheets_data = {}

    def fetch_data(sheet):
        try:
            worksheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet)
            data = worksheet.get_all_values()
            sheets_data[sheet] = data if data else []  # Ensure no NoneType errors
        except Exception as e:
            print(f"[ERROR] Failed to fetch data from sheet {sheet}: {e}")
            sheets_data[sheet] = []  # Prevent breaking other sheets

    # Use threading to fetch data from all sheets simultaneously
    threads = [threading.Thread(target=fetch_data, args=(sheet,)) for sheet in sheets]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return sheets_data  # Always returns a dictionary, even if some sheets failed

class TestScoreDetails(Resource):
    def get(self):
        """ Retrieves detailed test scores with optional batch filtering for student_ids and test_ids. """
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        
        # ✅ Allow both "student_id" and "student_ids"
        student_ids = request.args.get("student_ids") or request.args.get("student_id")
        test_ids = request.args.get("test_ids") or request.args.get("test_id")

        # Convert comma-separated values to lists
        if student_ids:
            student_ids = student_ids.split(",")
        if test_ids:
            test_ids = test_ids.split(",")

        # Validate API key and access to the requested students
        valid, error_message = validate_api_key_and_student(api_key, school_id, student_ids)
        if not valid:
            return jsonify({"error": error_message})

        # Fetch cached test score details from all sheets
        sheets_data = get_test_score_details()
        details = []

        for sheet_name, data in sheets_data.items():
            headers = data[0]
            for row in data[1:]:  # Skip header row
                school_name = row[0].strip()  # Ensure spaces are removed
                student_id = row[1].strip()
                test_id = row[2].strip()

                # ✅ Apply filtering conditions including school_name
                if (
                    school_name == school_id and  # Ensure school matches
                    (not student_ids or student_id in student_ids) and
                    (not test_ids or test_id in test_ids)
                ):
                    result = {
                        "school_name": school_name,
                        "student_id": student_id,
                        "test_id": test_id,
                        "test_date": row[3],
                        "score": row[4]
                    }
                    for i in range(5, len(row)):
                        if headers[i] and row[i]:
                            result[headers[i]] = row[i]
                    details.append(result)

        return jsonify(details if details else {"error": "No test details found."})

# =============================================
# 🩺 USMLE RESULTS ENDPOINT (Batch Processing)
# =============================================
@cache.cached(timeout=300)
def get_all_usmle_results():
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("usmle_results")
    data = sheet.get_all_values()
    
    # ✅ Ensure an empty list is returned if no data is found
    if not data or len(data) < 2:  # Header row is always there
        return []

    return [{"student_id": row[1], "test_id": row[2], "test_date": row[3], "result": row[4]} for row in data[1:]]

class USMLEResults(Resource):
    def get(self):
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        
        # ✅ Support both "student_id" and "student_ids" and split correctly
        student_ids = request.args.get("student_ids") or request.args.get("student_id")

        # ✅ Ensure it's always a list (even if empty)
        student_ids = student_ids.split(",") if student_ids else []

        # ✅ Trim whitespace from each student_id
        student_ids = [sid.strip() for sid in student_ids]

        valid, error_message = validate_api_key_and_student(api_key, school_id, student_ids)
        if not valid:
            return jsonify({"error": error_message})

        all_results = get_all_usmle_results()

        # ✅ Prevent NoneType errors
        if all_results is None:
            return jsonify({"error": "No USMLE results available."})

        # ✅ If student_ids is empty, return all results for the school
        student_results = [
            result for result in all_results 
            if not student_ids or result["student_id"] in student_ids
        ]

        return jsonify(student_results if student_results else {"error": "No USMLE results found for the specified students."})


# Health Check Endpoint (to setup automated pings to prevent cold starts)

class HealthCheck(Resource):
    def get(self):
        return jsonify({"status": "ok"})

# Register all endpoints
api.add_resource(ExamStats, "/api/exam-stats")
api.add_resource(AvailableTests, "/api/tests")
api.add_resource(Students, "/api/students")
api.add_resource(StudentScores, "/api/students/scores")
api.add_resource(StudentTests, "/api/students/tests")
api.add_resource(TestScoreDetails, "/api/students/scores/details")
api.add_resource(USMLEResults, "/api/students/usmle-results")
api.add_resource(HealthCheck, "/api/health")

# Run Flask App

# ✅ Debugging: Print all registered routes in Render logs
print("[INFO] Registered Routes:")
for rule in app.url_map.iter_rules():
    print(f"{rule} -> {rule.endpoint}")

# ✅ Add a simple homepage route
@app.route("/")
def home():
    return jsonify({"message": "Flask API is running!"})

if __name__ == "__main__":
    app.run(debug=True)


