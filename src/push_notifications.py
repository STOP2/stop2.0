from pyfcm import FCMNotification
import db

push_service = FCMNotification(api_key='AIzaSyDzuwUdIs5sog6UAv1dTzx2JJuCG2yOkcA')

database = db.Database()
devices = database.get_device_ids()
#lisättävä ehto, joka rajaa notifikaation saajat
registration_ids = [devices]
message_title = "Bussi saapuu!"
message_body = "Tilaamasi bussi saapuu pysäkillesi hetken kuluttua"
result = push_service.notify_multiple_devices(registration_ids=registration_ids, message_title=message_title, message_body=message_body)