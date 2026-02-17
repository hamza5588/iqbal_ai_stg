
import requests
import json
import sys

BASE_URL = "http://localhost:5000"

def debug_rag():
    print(f"Testing RAG Chat on {BASE_URL}...")
    
    # 1. Login to get a session cookie
    session = requests.Session()
    # Assuming a known test user exists or we can just hit the endpoint if we have a valid session
    # Actually, let's try to hit the endpoint directly. If it fails with 401, we know auth is working.
    # But for 500, auth probably passed.
    
    # Let's try to create a conversation first (needs auth)
    # We need a valid user. Let's list users first? No, admin only.
    # Let's try to login as a test user.
    login_payload = {
        "useremail": "loadtest_teacher_1_2_abcd@test.iqbalai.com", # Replace with profound user
        "password": "password123"
    }
    
    # We need to find a valid user from the DB first? 
    # Or just try to hit the chat endpoint with a made-up thread_id and see if it explodes before auth.
    # Actually, the 500 error happened inside the chat function, meaning auth likely passed.
    
    # Let's try to reproduce with a mock request that mimics what the test does.
    # The test sends: message, thread_id, conversation_id.
    
    payload = {
        "message": "Debug test message",
        "thread_id": "test_thread_123", # Likely invalid, but should return 400 not 500
        "conversation_id": "999"
    }
    
    print(f"Sending payload: {json.dumps(payload)}")
    
    try:
        # We need a valid session. 
        # I'll manually login with a known admin or test user if possible. 
        # Since I can't easily know a valid user password without checking DB, 
        # I will rely on the fact that the previous test run DID login successfully (logs showed it).
        # Wait, the logs showed "Chat HTTP error... 500".
        
        # PROPOSAL: I will use the "login" endpoint with the same user the test used?
        # The test creates users dynamically.  
        
        # ALTERNATIVE: I'll construct a request that hits the endpoint.
        # But without a valid session, I'll get 401. 
        # The 500 happened *after* login.
        
        # Okay, let's look at the `rag_routes.py` again.
        # The error "name 'llm' is not defined" happened.
        # I fixed it by replacing `llm` with `user_llm` or `get_rag_llm(...)`.
        
        # HYPOTHESIS: I might have introduced a SyntaxError or IndentationError in my last edit to `rag_service.py`.
        # The `replace_file_content` might have messed up the indentation of the try-except block.
        
        pass
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    debug_rag()
