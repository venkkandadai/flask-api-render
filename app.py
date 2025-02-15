from flask import Flask, request, jsonify
from flask_restful import Api, Resource
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask_caching import Cache  # ✅ Import caching
import threading
import os

# Initialize Flask App
app = Flask(__name__)
app.config['CACHE_TYPE'] = 'simple'  # ✅ Configure caching
cache = Cache(app)  # ✅ Initialize cache
api = Api(app)

# Google Sheets API Setup
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# ✅ Use different paths for local vs. Render environment
GOOGLE_CREDENTIALS_PATH = "vivid-monitor-451014-a7-0a8a581b3c3a.json"  # Local path

if os.getenv("RENDER"):  # ✅ If running on Render, use the secret file
    GOOGLE_CREDENTIALS_PATH = "/var/data/vivid-monitor-451014-a7-0a8a581b3c3a.json"

CREDS = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_PATH, SCOPE)
client = gspread.authorize(CREDS)

# Spreadsheet ID
SPREADSHEET_ID = "1em9fNvDv22xtCnaonn_gB5ThgucD922R9p9svF_ptUs"

# API Keys
API_KEYS = {
    "003fb7e922cd6595f4243703b7d3a32f": "MedSchoolA",
    "2d7ebd0c14a5c41d18172341920cd222": "MedSchoolB",
    "9198340729aae63e06785df4cd61d8b2": "MedSchoolC",
    "20e8b4cad8f774a7bbe076029ba3a38c": "MedSchoolD",
    "a71ed21d7da1aead4e5088827d1c67fc": "MasterKey"
}

# Validate API Key and Student Access
def validate_api_key_and_student(api_key, school_id, student_id=None):
    if api_key not in API_KEYS:
        return False, "Invalid API key."
    assigned_school = API_KEYS[api_key]
    if assigned_school != "MasterKey" and assigned_school != school_id:
        return False, "Access denied."
    
    if student_id:
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("roster_data")
        data = sheet.get_all_values()
        student_exists = any(row[1] == student_id and row[0] == school_id for row in data[1:])
        if not student_exists:
            return False, "Student does not belong to the provided school."
    
    return True, None

# Fetch exam statistics
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

# Fetch available tests
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

# Fetch students
class Students(Resource):
    def get(self):
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        valid, error_message = validate_api_key_and_student(api_key, school_id)
        if not valid:
            return jsonify({"error": error_message})
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("roster_data")
        data = sheet.get_all_values()
        students = [{"student_id": row[1], "first_name": row[2], "last_name": row[3]} for row in data[1:] if row[0] == school_id]
        return jsonify(students if students else {"error": "No students found."})

# Fetch student scores with optimized sheet access
@cache.cached(timeout=300)
def get_student_scores():
    sheets = ["se_scores", "cas_scores", "nsas_scores"]
    sheets_data = {}

    def fetch_data(sheet):
        sheets_data[sheet] = client.open_by_key(SPREADSHEET_ID).worksheet(sheet).get_all_values()

    threads = [threading.Thread(target=fetch_data, args=(sheet,)) for sheet in sheets]
    
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return sheets_data

class StudentScores(Resource):
    def get(self):
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        student_id = request.args.get("student_id")

        valid, error_message = validate_api_key_and_student(api_key, school_id, student_id)
        if not valid:
            return jsonify({"error": error_message})

        sheets_data = get_student_scores()
        scores = []

        print(f"[DEBUG] Checking for student_id: {student_id}")  # ✅ Debug print
        for sheet_name, data in sheets_data.items():
            print(f"[DEBUG] Checking sheet: {sheet_name}")  # ✅ Print sheet being checked
            for row in data[1:]:
                print(f"[DEBUG] Row Data: {row}")  # ✅ Print each row being checked
                if row[1] == student_id:
                    scores.append({
                        "student_id": row[1],
                        "test_id": row[2],
                        "test_date": row[3],
                        "score": row[4]
                    })

        print(f"[DEBUG] Final Scores Output: {scores}")  # ✅ Print final scores list
        return jsonify(scores if scores else {"error": "No scores found."})

# Fetch student tests
class StudentTests(Resource):
    def get(self):
        return StudentScores().get()

# Fetch detailed test scores with optimized sheet access
@cache.cached(timeout=300)
def get_test_score_details():
    sheets = ["se_scores", "nsas_scores"]
    sheets_data = {sheet: client.open_by_key(SPREADSHEET_ID).worksheet(sheet).get_all_values() for sheet in sheets}
    return sheets_data

class TestScoreDetails(Resource):
    def get(self):
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        student_id = request.args.get("student_id")
        test_id = request.args.get("test_id")
        valid, error_message = validate_api_key_and_student(api_key, school_id, student_id)
        if not valid:
            return jsonify({"error": error_message})
        
        sheets_data = get_test_score_details()
        details = []
        for sheet_name, data in sheets_data.items():
            headers = data[0]
            for row in data[1:]:
                if row[1] == student_id and row[2] == test_id:
                    result = {"student_id": row[1], "test_id": row[2], "test_date": row[3], "score": row[4]}
                    for i in range(5, len(row)):
                        if headers[i] and row[i]:
                            result[headers[i]] = row[i]
                    details.append(result)
        
        return jsonify(details if details else {"error": "No test details found."})

# Fetch USMLE results
@cache.cached(timeout=300)  # ✅ Cache USMLE results for 5 minutes
def get_all_usmle_results():
    """Fetches all USMLE results from Google Sheets and caches them."""
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("usmle_results")
    data = sheet.get_all_values()
    return [
        {"student_id": row[1], "test_id": row[2], "test_date": row[3], "result": row[4]}
        for row in data[1:]
    ]

class USMLEResults(Resource):
    def get(self):
        api_key = request.args.get("api_key")
        school_id = request.args.get("school_id")
        student_id = request.args.get("student_id")

        # ✅ Validate API key and student-school relationship
        valid, error_message = validate_api_key_and_student(api_key, school_id, student_id)
        if not valid:
            return jsonify({"error": error_message})

        # ✅ Fetch cached USMLE results and filter for the requested student
        all_results = get_all_usmle_results()
        student_results = [result for result in all_results if result["student_id"] == student_id]

        return jsonify(student_results if student_results else {"error": "No USMLE results found."})

# Register all endpoints
api.add_resource(ExamStats, "/api/exam-stats")
api.add_resource(AvailableTests, "/api/tests")
api.add_resource(Students, "/api/students")
api.add_resource(StudentScores, "/api/students/scores")
api.add_resource(StudentTests, "/api/students/tests")
api.add_resource(TestScoreDetails, "/api/students/scores/details")
api.add_resource(USMLEResults, "/api/students/usmle-results")

# Run Flask App
if __name__ == "__main__":
    app.run(debug=True)

