"""Insert RMFYALK token into config.yaml"""
import os

token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE3ODg5NTMzNDAsInVzZXJuYW1lIjoiRUxqT3dORk1XbklHVW02MTlKcThUaW5qY01LakU5V1I0N2VSeWtoOWdlTXZndXkxSzRsWXZWekVvRnFZa0hnR0xGdnRoaW5NZ2srUVxuYW9SbEhldDdNNzVRRGc0bjB0S29tUlQzK1pxeWR1QT0ifQ.tGv7EMHyMiIRACakn7jrn-K_SztSOb8z1UecYC8I7ik"

config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config.yaml')
with open(config_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
inserted = False
for i, line in enumerate(lines):
    new_lines.append(line)
    if 'rmfyalk_pages_per_keyword' in line and not inserted:
        new_lines.append('  rmfyalk_token: "' + token + '"\n')
        new_lines.append('  rmfyalk_pool_min_pages: 3\n')
        inserted = True
        print(f"Inserted token after line {i+1}")

if not inserted:
    print("ERROR: Could not find rmfyalk_pages_per_keyword line")
else:
    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print("Config updated successfully")
