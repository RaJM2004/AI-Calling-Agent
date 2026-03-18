import os
import pandas as pd
import requests
from dotenv import load_dotenv
from twilio.rest import Client
from datetime import datetime
import json
import time
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO
import re
from groq import Groq

# Load environment variables
load_dotenv()

# Initialize Groq client
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Initialize Flask app and SocketIO
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)

# Initialize Twilio client
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), 
                      os.getenv("TWILIO_AUTH_TOKEN"))

# Vapi configuration
vapi_headers = {
    "Authorization": f"Bearer {os.getenv('VAPI_API_KEY')}",
    "Content-Type": "application/json"
}

# File paths
INPUT_EXCEL = "updated_client.xlsx"
OUTPUT_EXCEL = "client_response.xlsx"
BACKUP_EXCEL = "client_responses_backup.xlsx"

def log(message):
    """Prints a message and emits it over a socket."""
    print(message)
    socketio.emit('log_message', {'data': str(message)})

# ... (keep all the imports and setup code the same until the Groq section)

def process_transcript_with_groq(transcript):
    """
    Processes a transcript using the Groq API to extract cybersecurity meeting information.
    """
    if not transcript:
        return "", "", "", ""

    try:
        log("🤖 Calling Groq API to process transcript...")
        
        prompt = f"""
        Analyze the following cybersecurity consultation transcript and provide the following information in JSON format:
        
        1. **Summary**: A comprehensive summary of the cybersecurity meeting including:
           - Client's main security concerns
           - Threats identified or discussed
           - Solutions proposed
           - Action items agreed upon
           - Timeline discussed (if any)
        
        2. **Checklist**: A 3-point priority checklist based on the transcript:
           - Immediate action items (high priority)
           - Short-term security measures
           - Long-term security strategy
        
        3. **SMS Message**: A concise SMS message to be sent to the client containing:
           - Key decisions from the meeting
           - Next steps agreed upon
           - Any immediate action items
           - At the bottom, add company info: "For more details: https://anvriksh.com/"
        
        Transcript:
        ---
        {transcript}
        ---

        Output should be a JSON object with the following keys: "summary", "checklist", "sms_message".
        The checklist should be an array of 3 strings.
        The SMS message should be professional and include the company URL at the end.
        """
        
        # Updated list of currently available Groq models (as of Jan 2026)
        available_models = [
            "llama-3.3-70b-versatile",      # Latest Llama 3.3 model
            "llama-3.2-1b-preview",         # Lightweight model
            "llama-3.2-3b-preview",         # Balanced model
            "llama-3.2-90b-vision-preview", # High capacity model
            "llama-3.2-11b-vision-preview", # Vision capable model
            "llama-3.1-8b-instant",         # Fast model
            "llama-3.1-70b-versatile",      # Versatile model
            "llama-3.1-405b-versatile",     # Large model (if available)
            "gemma2-9b-it",                  # Google's Gemma 2
            "mixtral-8x7b-32768",           # Mixtral (might still work for some)
        ]
        
        last_error = None
        successful_model = None
        response_text = None
        
        for model in available_models:
            try:
                log(f"  Trying model: {model}")
                chat_completion = groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a cybersecurity expert analyzing consultation calls. Extract key information and provide structured output in JSON format."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    model=model,
                    temperature=0.3,
                    max_tokens=1024,
                    top_p=1,
                    stream=False
                    # Removed response_format parameter as some models might not support it
                )
                
                response_text = chat_completion.choices[0].message.content
                successful_model = model
                log(f"✅ Successfully used model: {model}")
                break  # Exit loop if successful
                
            except Exception as e:
                last_error = e
                log(f"  Model {model} failed: {str(e)[:100]}...")
                time.sleep(0.5)  # Small delay between attempts
                continue  # Try next model
        
        # Check if we got a response
        if not response_text:
            log("⚠️ No model worked. Using fallback response.")
            # Create fallback response
            checklist = ["1. Review security assessment", "2. Implement basic protections", "3. Schedule follow-up meeting"]
            sms_message = f"Thank you for the cybersecurity consultation. We've noted your security concerns and will provide detailed recommendations soon.\n\nFor more details: https://anvriksh.com/"
            return "AI service temporarily unavailable. Please review transcript manually.", "\n".join(checklist), sms_message, ""
        
        log(f"📝 Groq API Response from {successful_model}: {response_text[:200]}...")

        # Clean the response to extract the JSON part
        cleaned_response = response_text.strip()
        
        # Try to extract JSON from various formats
        import re
        
        # Look for JSON in the response
        json_match = re.search(r'\{.*\}', cleaned_response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                # If JSON parsing fails, try to extract key information
                data = {}
        else:
            # If no JSON found, create structured data from text
            data = {
                "summary": cleaned_response[:500] + "..." if len(cleaned_response) > 500 else cleaned_response,
                "checklist": [
                    "1. Review the full consultation notes",
                    "2. Prioritize security recommendations",
                    "3. Plan implementation timeline"
                ],
                "sms_message": f"Thank you for discussing your cybersecurity needs. We'll prepare customized recommendations based on our conversation.\n\nFor more details: https://anvriksh.com/"
            }
        
        summary = data.get("summary", "")
        checklist = data.get("checklist", [])
        sms_message = data.get("sms_message", "")
        
        # Ensure checklist is a list
        if isinstance(checklist, str):
            # Try to parse checklist string into list
            checklist_items = [item.strip() for item in checklist.split('\n') if item.strip()]
            if len(checklist_items) >= 3:
                checklist = checklist_items[:3]
            else:
                checklist = ["1. Assess current security posture", "2. Identify critical vulnerabilities", "3. Develop mitigation strategy"]
        
        # Ensure we have exactly 3 checklist items
        while len(checklist) < 3:
            checklist.append(f"{len(checklist)+1}. Additional security measure to be determined")
        
        # Ensure company URL is included in SMS
        if sms_message and "anvriksh.com" not in sms_message.lower():
            sms_message += "\n\nFor more details: https://anvriksh.com/"
        
        # Format checklist as a string
        checklist_str = "\n".join(f"- {item}" for item in checklist[:3])
        
        log("✅ Successfully processed transcript with Groq.")
        return summary, checklist_str, sms_message, ""
        
    except json.JSONDecodeError as e:
        log(f"❌ Error parsing JSON response from Groq: {e}")
        # Create fallback response
        checklist = ["1. Review security logs", "2. Update security policies", "3. Train staff on security best practices"]
        sms_message = f"Thank you for the cybersecurity consultation. We've documented your requirements and will contact you with a detailed proposal.\n\nFor more details: https://anvriksh.com/"
        return "Parsed conversation summary available. Detailed analysis requires manual review.", "\n".join(checklist), sms_message, ""
    
    except Exception as e:
        log(f"❌ Error processing transcript with Groq: {e}")
        # Return comprehensive fallback values
        checklist = [
            "1. Conduct security assessment",
            "2. Implement firewall and antivirus",
            "3. Schedule employee security training"
        ]
        sms_message = f"Thank you for your cybersecurity consultation with Anvriksh. We've recorded your security concerns and will provide tailored recommendations. Our team will contact you within 24-48 hours with next steps.\n\nFor more details: https://anvriksh.com/"
        return f"Could not process transcript with AI: {str(e)[:100]}", "\n".join(checklist), sms_message, ""


def initialize_data():
    """Load and prepare data with enhanced validation - FLEXIBLE COLUMN HANDLING"""
    try:
        log(f"🔄 Loading data from {INPUT_EXCEL}")
        df = pd.read_excel(INPUT_EXCEL)
        log("✅ Data loaded successfully")
        log(f"📋 Columns found: {list(df.columns)}")
        log("📋 Sample data:")
        log(df.head(3).to_string())
        
        # Clean column names (remove extra spaces, lowercase)
        df.columns = df.columns.str.strip().str.lower()
        
        # Map possible column names to standard names
        column_mapping = {
            # Name columns
            'full name': ['full name', 'name', 'client name', 'customer name', 'fullname'],
            'first name': ['first name', 'firstname', 'fname'],
            'last name': ['last name', 'lastname', 'lname'],
            
            # Phone columns
            'phone_number': ['phone_number', 'phone', 'mobile', 'contact', 'phone number', 
                           'mobile number', 'contact number', 'phonenumber'],
            
            # Email columns
            'email': ['email', 'email address', 'emailid', 'e-mail'],
            
            # Interest columns - accept various formats
            'interest_status': [
                'are_you_interested_in_buying_a_flat_at_kukatpally?',
                'are you interested in buying a flat at kukatpally?',
                'interested_in_cybersecurity_consultation?',
                'interested in cybersecurity consultation?',
                'interest_in_security_audit',
                'security_interest',
                'interest_status',
                'interested',
                'interest'
            ]
        }
        
        # Function to find and rename columns
        def find_and_rename_column(df, target_name, possible_names):
            for name in possible_names:
                if name in df.columns:
                    if name != target_name:
                        df.rename(columns={name: target_name}, inplace=True)
                        log(f"  ↳ Renamed '{name}' to '{target_name}'")
                    return True
            return False
        
        # Apply column mapping
        required_found = []
        for target_name, possible_names in column_mapping.items():
            if find_and_rename_column(df, target_name, possible_names):
                if target_name in ['full name', 'phone_number']:
                    required_found.append(target_name)
        
        # Check if we have at least name and phone
        if not all(col in df.columns for col in ['full name', 'phone_number']):
            # Try to create full name from first and last name
            if 'first name' in df.columns and 'last name' in df.columns:
                df['full name'] = df['first name'].astype(str) + ' ' + df['last name'].astype(str)
                required_found.append('full name')
                log("  ↳ Created 'full name' from 'first name' and 'last name'")
            
            # Check for phone in other formats
            phone_aliases = ['phone', 'mobile', 'contact']
            for alias in phone_aliases:
                if alias in df.columns and 'phone_number' not in df.columns:
                    df['phone_number'] = df[alias]
                    required_found.append('phone_number')
                    log(f"  ↳ Using '{alias}' as phone_number")
                    break
        
        # Final check for required columns
        if 'full name' not in df.columns:
            raise ValueError(f"Cannot find name column. Available columns: {list(df.columns)}")
        
        if 'phone_number' not in df.columns:
            raise ValueError(f"Cannot find phone number column. Available columns: {list(df.columns)}")
        
        # Clean and format phone numbers
        df["phone_number"] = df["phone_number"].astype(str).str.replace(r'\D+', '', regex=True).str.strip()
        
        # Keep only digits for Indian numbers
        df["phone_number"] = df["phone_number"].apply(lambda x: x[-10:] if len(x) >= 10 else x)
        
        # Format as E.164 for Indian numbers
        df["phone_number"] = "+91" + df["phone_number"]
        
        # Clean names
        if 'full name' in df.columns:
            df['full name'] = df['full name'].astype(str).str.strip()
        
        # Handle interest status - create if not present
        if 'interest_status' not in df.columns:
            df['interest_status'] = 'yes'  # Default to yes if not specified
            log("  ↳ Added default 'interest_status' column with 'yes'")
        else:
            df['interest_status'] = df['interest_status'].astype(str).str.lower().str.strip()
            # Map various responses to yes/no
            yes_values = ['yes', 'y', 'true', '1', 'interested', 'sure', 'ok']
            no_values = ['no', 'n', 'false', '0', 'not interested', 'decline']
            
            def map_interest(value):
                if pd.isna(value) or value == '':
                    return 'no'
                value = str(value).lower().strip()
                if value in yes_values:
                    return 'yes'
                elif value in no_values:
                    return 'no'
                else:
                    return 'yes'  # Default to yes for ambiguous responses
            
            df['interest_status'] = df['interest_status'].apply(map_interest)
        
        log(f"✅ Data prepared successfully. Columns: {list(df.columns)}")
        return df
    except Exception as e:
        log(f"❌ Error loading Excel file: {str(e)}")
        raise

def initialize_responses():
    """Initialize or load response tracking with backup system"""
    try:
        if os.path.exists(OUTPUT_EXCEL):
            log(f"📂 Loading existing responses from {OUTPUT_EXCEL}")
            df = pd.read_excel(OUTPUT_EXCEL)
            
            # Create backup
            df.to_excel(BACKUP_EXCEL, index=False)
            log(f"🔐 Created backup at {BACKUP_EXCEL}")
            
            return df
        else:
            log("🆕 Creating new responses file")
            columns = [
                'timestamp', 'full_name', 'phone_number', 'interest_status',
                'call_id', 'call_status', 'security_assessment_date', 'sms_status',
                'sms_content', 'summary', 'checklist', 'call_report', 'transcript', 'recording_url'
            ]
            return pd.DataFrame(columns=columns)
    except Exception as e:
        log(f"❌ Error initializing responses: {str(e)}")
        raise

def make_vapi_call(name, phone):
    """Make call using Vapi with improved error handling"""
    try:
        # Verify phone number format
        if not phone.startswith("+"):
            phone = f"+91{phone[-10:]}"
        
        payload = {
            "assistantId": os.getenv("VAPI_ASSISTANT_ID"),
            "customer": {
                "number": phone,
                "name": name
            },
            "metadata": {
                "client_name": name,
                "purpose": "cybersecurity_consultation"
            },
            "type": "outboundPhoneCall",
            "phoneNumber": {
                "twilioPhoneNumber": os.getenv("TWILIO_FROM_NUMBER"),
                "twilioAccountSid": os.getenv("TWILIO_ACCOUNT_SID"),
                "twilioAuthToken": os.getenv("TWILIO_AUTH_TOKEN")
            }
        }
        
        log("📡 Sending call request to VAPI...")
        response = requests.post(
            "https://api.vapi.ai/call",
            headers=vapi_headers,
            json=payload,
            timeout=30
        )
        
        response.raise_for_status()
        call_data = response.json()
        
        log(f"✅ Call initiated successfully. Call ID: {call_data.get('id')}")
        return call_data.get("id"), "queued"
        
    except requests.exceptions.RequestException as e:
        error_msg = f"API request failed: {str(e)}"
        if hasattr(e, 'response') and e.response:
            error_msg += f" | Response: {e.response.text}"
        log(f"❌ {error_msg}")
        return None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        log(f"❌ {error_msg}")
        return None, error_msg

def monitor_call(call_id, timeout=300):
    """Monitor call with status tracking and timeout"""
    start_time = time.time()
    last_status = ""
    
    try:
        while time.time() - start_time < timeout:
            response = requests.get(
                f"https://api.vapi.ai/call/{call_id}",
                headers=vapi_headers,
                timeout=30
            )
            
            response.raise_for_status()
            call_data = response.json()
            current_status = call_data.get("status", "")
            
            if current_status != last_status:
                log(f"🔄 Call status changed: {current_status}")
                last_status = current_status
            
            if current_status in ["ended", "fulfilled"]:
                security_assessment_date = call_data.get("metadata", {}).get("assessment_date", "")
                transcript = call_data.get("transcript", "")
                recording_url = call_data.get("recordingUrl", "")
                
                log(f"✅ Call completed with status: {current_status}")
                
                return current_status, security_assessment_date, transcript, recording_url
                
            elif current_status == "failed":
                return "failed", "", "", ""
            
            time.sleep(5)
        
        log("⏰ Call monitoring timeout reached")
        return "timeout", "", "", ""
        
    except Exception as e:
        log(f"❌ Error monitoring call: {str(e)}")
        return "error", "", "", ""

def send_sms(phone, message_body):
    """Send SMS with Twilio with retry logic"""
    max_retries = 2
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            phone = str(phone).strip()
            if not phone.startswith("+"):
                phone = f"+91{phone[-10:]}"
            
            message = twilio_client.messages.create(
                body=message_body,
                from_=os.getenv("TWILIO_FROM_NUMBER"),
                to=phone
            )
            
            log(f"📱 SMS sent successfully (SID: {message.sid})")
            return "sent", message_body
            
        except Exception as e:
            if attempt == max_retries - 1:
                error_msg = f"Failed to send SMS after {max_retries} attempts: {str(e)}"
                log(f"❌ {error_msg}")
                return "failed", error_msg
            time.sleep(retry_delay)
    
    return "failed", "Unknown error"

def save_responses(responses_df, data):
    """Save responses with atomic write pattern"""
    try:
        # Create temp file path
        temp_file = f"temp_{OUTPUT_EXCEL}"
        
        # Save to temp file first
        responses_df = pd.concat([responses_df, pd.DataFrame([data])], ignore_index=True)
        responses_df.to_excel(temp_file, index=False)
        
        # Verify the temp file was created
        if not os.path.exists(temp_file):
            raise Exception("Temp file not created")
        
        # Replace original file
        os.replace(temp_file, OUTPUT_EXCEL)
        
        log(f"💾 Data saved successfully to {OUTPUT_EXCEL}")
        return responses_df, True
        
    except Exception as e:
        log(f"❌ Error saving responses: {str(e)}")
        
        # Attempt to save to backup location
        try:
            backup_path = f"emergency_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            responses_df.to_excel(backup_path, index=False)
            log(f"⚠️ Saved emergency backup to {backup_path}")
        except:
            log("🔥 CRITICAL: Failed to create emergency backup")
        
        return responses_df, False

@socketio.on('connect')
def handle_connect():
    log('Client connected')

@app.route('/vapi-webhook', methods=['POST'])
def handle_webhook():
    """Enhanced webhook handler with validation"""
    try:
        log("\n" + "="*50)
        log("📩 Webhook Received")
        log("="*50)
        
        # Log full request details
        log(f"📦 Headers: {dict(request.headers)}")
        log(f"📝 Raw data: {request.data[:500]}...")  # Log first 500 chars
        
        data = request.json
        if not data:
            raise ValueError("Empty request body")
        
        call_id = data.get('id')
        status = data.get('status', 'unknown')
        
        log(f"🔔 Call ID: {call_id} | Status: {status}")
        
        # Initialize DataFrame
        if os.path.exists(OUTPUT_EXCEL):
            df = pd.read_excel(OUTPUT_EXCEL)
        else:
            df = pd.DataFrame(columns=[
                'timestamp', 'full_name', 'phone_number', 'call_id', 
                'call_status', 'security_assessment_date', 'transcript', 'recording_url',
                'summary', 'checklist', 'sms_status', 'sms_content'
            ])
        
        # Prepare update data
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        metadata = data.get('metadata', {})
        transcript = data.get('transcript', '')
        
        summary, checklist, sms_message, _ = "", "", "", ""
        if transcript:
            summary, checklist, sms_message, _ = process_transcript_with_groq(transcript)

        security_assessment_date = metadata.get('assessment_date', '')
        
        update_data = {
            'timestamp': update_time,
            'call_status': status,
            'security_assessment_date': security_assessment_date,
            'transcript': transcript,
            'recording_url': data.get('recordingUrl', ''),
            'summary': summary,
            'checklist': checklist
        }
        
        # Update existing record or create new
        if call_id and 'call_id' in df.columns and call_id in df['call_id'].values:
            idx = df[df['call_id'] == call_id].index[0]
            log(f"🔄 Updating existing record for call {call_id}")
            
            # Get client details for SMS if call just ended
            if status in ['ended', 'fulfilled']:
                client_name = df.loc[idx, 'full_name']
                client_phone = df.loc[idx, 'phone_number']
                if sms_message:
                    sms_status, sms_content = send_sms(client_phone, sms_message)
                else:
                    # Fallback SMS for cybersecurity consultation
                    sms_status, sms_content = send_sms(client_phone, f"Hi {client_name}, thank you for the cybersecurity consultation. We've documented your security concerns and will follow up with detailed recommendations.\n\nFor more details: https://anvriksh.com/")
                update_data['sms_status'] = sms_status
                update_data['sms_content'] = sms_content
        else:
            idx = len(df)
            df.loc[idx, 'call_id'] = call_id
            log(f"🆕 Creating new record for call {call_id}")
        
        # Apply updates
        for col, val in update_data.items():
            if val:  # Only update non-empty values
                df.loc[idx, col] = val
        
        # Save with atomic write
        temp_file = "temp_webhook.xlsx"
        df.to_excel(temp_file, index=False)
        os.replace(temp_file, OUTPUT_EXCEL)
        
        log(f"✅ Successfully updated {OUTPUT_EXCEL}")
        return jsonify({"success": True, "message": "Update processed"}), 200
        
    except Exception as e:
        log(f"❌ Webhook processing error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

def run_flask_app():
    """Run Flask with production settings"""
    socketio.run(app, host='0.0.0.0', port=5000)

@app.route('/')
def serve_dashboard():
    return send_from_directory(os.getcwd(), 'dashboard.html')

@app.route('/get-clients')
def get_clients():
    try:
        # This function is now less critical for real-time updates, 
        # but still useful for initial loading.
        if os.path.exists(OUTPUT_EXCEL):
            try:
                if os.path.getsize(OUTPUT_EXCEL) > 0:
                    df = pd.read_excel(OUTPUT_EXCEL)
                    df = df.fillna('').astype(str)
                    return jsonify({"clients": df.to_dict(orient='records')})
            except Exception as e:
                log(f"Could not read {OUTPUT_EXCEL}: {e}. Falling back.")
        
        if os.path.exists(INPUT_EXCEL):
            try:
                if os.path.getsize(INPUT_EXCEL) > 0:
                    df = pd.read_excel(INPUT_EXCEL)
                    df = df.fillna('').astype(str)
                    return jsonify({"clients": df.to_dict(orient='records')})
            except Exception as e:
                log(f"Could not read {INPUT_EXCEL}: {e}.")

        return jsonify({"clients": []})

    except Exception as e:
        log(f"A critical error occurred in /get-clients: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/upload-excel', methods=['POST'])
def upload_excel():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    if file:
        file.save(INPUT_EXCEL)
        df = pd.read_excel(INPUT_EXCEL)
        return jsonify({"count": len(df), "clients": df.to_dict(orient='records')})

@app.route('/start-campaign', methods=['POST'])
def start_campaign():
    socketio.start_background_task(target=run_campaign)
    return jsonify({"message": "Campaign started"})

@app.route('/stop-campaign', methods=['POST'])
def stop_campaign():
    # This is a placeholder, as stopping the campaign is not implemented
    return jsonify({"message": "Campaign stopping is not implemented"})

def run_campaign():
    try:
        # Check for essential environment variables
        required_env_vars = [
            "VAPI_API_KEY", "VAPI_ASSISTANT_ID", "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "GROQ_API_KEY"
        ]
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            log(f"❌ Critical Error: Missing environment variables: {', '.join(missing_vars)}")
            log("Please ensure your .env file is correctly set up.")
            return # Stop the campaign

        # Initialize data
        df = initialize_data()
        responses_df = initialize_responses()
        
        # Process each client
        for idx, row in df.iterrows():
            log("\n" + "="*50)
            log(f"👤 Processing client {idx+1}/{len(df)}: {row['full name']}")
            log("="*50)
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            name = row["full name"]
            phone = row["phone_number"]
            interest = row.get("interest_status", "yes")  # Default to yes if not present
            
            # Initialize response data
            response_data = {
                'timestamp': timestamp,
                'full_name': name,
                'phone_number': phone,
                'interest_status': interest,
                'call_id': "",
                'call_status': "",
                'security_assessment_date': "",
                'sms_status': "",
                'sms_content': "",
                'summary': "",
                'checklist': "",
                'call_report': "",
                'transcript': "",
                'recording_url': ""
            }
            
            # Skip if not interested
            if interest != "yes":
                response_data.update({
                    'call_status': "not attempted",
                    'sms_status': "not attempted",
                    'notes': "Client not interested"
                })
                responses_df, _ = save_responses(responses_df, response_data)
                log(f"⏩ Skipped - Client not interested (status: {interest})")
                continue
            
            # Make the call
            call_id, call_status = make_vapi_call(name, phone)
            response_data['call_id'] = call_id or ""
            response_data['call_status'] = call_status or ""
            
            if not call_id:
                response_data['notes'] = "Call initiation failed"
                responses_df, _ = save_responses(responses_df, response_data)
                log("❌ Call failed to initiate")
                continue
            
            # Monitor call
            log(f"🔍 Monitoring call {call_id}...")
            final_status, security_assessment_date, transcript, recording_url = monitor_call(call_id)
            
            # Update response data
            response_data.update({
                'call_status': final_status,
                'transcript': transcript,
                'recording_url': recording_url,
                'security_assessment_date': security_assessment_date
            })

            summary, checklist, sms_message, _ = "", "", "", ""
            if transcript:
                summary, checklist, sms_message, _ = process_transcript_with_groq(transcript)
            
            response_data.update({
                'summary': summary,
                'checklist': checklist,
            })
            
            # Send SMS with consultation summary
            if sms_message:
                sms_status, sms_content = send_sms(phone, sms_message)
                response_data.update({
                    'sms_status': sms_status,
                    'sms_content': sms_content,
                    'notes': "Cybersecurity consultation completed"
                })
            else:
                # Fallback SMS for cybersecurity
                sms_status, sms_content = send_sms(phone, f"Hi {name}, thank you for the cybersecurity consultation. We've documented your security concerns and will follow up with detailed recommendations.\n\nFor more details: https://anvriksh.com/")
                response_data.update({
                    'sms_status': sms_status,
                    'sms_content': sms_content,
                    'notes': "Cybersecurity consultation completed (fallback SMS)"
                })
            
            # Final save
            responses_df, success = save_responses(responses_df, response_data)
            if success:
                log(f"✅ Completed processing for {name}")
            else:
                log(f"⚠️ Processing completed but save failed for {name}")
        
        log("\n" + "="*50)
        log("🏁 Processing Complete")
        log("="*50)
        log(f"📊 Total clients processed: {len(df)}")
        log(f"💾 Final data saved to: {os.path.abspath(OUTPUT_EXCEL)}")
        
    except Exception as e:
        log(f"\n🔥 Critical error in run_campaign: {str(e)}")
        log("Please check the error and try again")

if __name__ == '__main__':
    log("--- Starting Cybersecurity Consultation System ---")
    socketio.run(app, debug=True, use_reloader=False)