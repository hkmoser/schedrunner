import requests
url = "https://api.retool.com/v1/workflows/9137ef5f-895d-4978-a337-1c02677c46b3/startTrigger"
headers = { 'Content-Type': 'application/json', 'X-Workflow-Api-Key': 'retool_wk_9e79c62725364a5087c96fe4c2eafdcc' }
r = requests.post(url, headers=headers)
r.json()
