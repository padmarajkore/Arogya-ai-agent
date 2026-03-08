import os
from litellm import completion

os.environ["AWS_REGION_NAME"] = "us-east-1"
try:
    response = completion(
        model="bedrock/us.amazon.nova-lite-v1:0",
        messages=[{"role": "user", "content": "Hello"}]
    )
    print("us.amazon.nova-lite-v1:0: SUCCESS in us-east-1")
except Exception as e:
    print("us.amazon.nova-lite-v1:0 error:", e)

os.environ["AWS_REGION_NAME"] = "ap-south-1"
try:
    response = completion(
        model="bedrock/us.amazon.nova-lite-v1:0",
        messages=[{"role": "user", "content": "Hello"}]
    )
    print("us.amazon.nova-lite-v1:0: SUCCESS in ap-south-1")
except Exception as e:
    print("us.amazon.nova-lite-v1:0 error in ap-south-1:", e)
