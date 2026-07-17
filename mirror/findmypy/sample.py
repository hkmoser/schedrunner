from findmypy.base import FindMyPyManager
from findmypy.base import FindMyPyConnection
import os
from dotenv import load_dotenv

load_dotenv()

test_connection = FindMyPyConnection("joe@joemoser.com","mkyd-kvke-ymad-pxmz")
test = FindMyPyManager(test_connection, True)
test.init_devices_list()
print(test.devices)

