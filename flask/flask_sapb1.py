from flask import current_app, g
import pymssql
import datetime
from time import time, strftime
import decimal
from pythoncom import CoInitialize
import win32com.client.dynamic
#from flask_mail import Message
import smtplib


try:
    from flask import _app_ctx_stack as stack
except ImportError:
    from flask import _request_ctx_stack as stack


class SapB1ComAdaptor(object):
    """Adaptor contains SAP B1 COM object.
    """
    def __init__(self, config):
        CoInitialize()
        SAPbobsCOM = __import__(config['DIAPI'], globals(), locals(), [], -1)
        self.constants = SAPbobsCOM.constants
        self.company = company = SAPbobsCOM.Company()
        company.Server = config['SERVER']
        company.DbServerType = getattr(self.constants, config['DBSERVERTYPE'])
        company.LicenseServer = config['LICENSE_SERVER']
        company.CompanyDB = config['COMPANYDB']
        company.UserName = config['B1USERNAME']
        company.Password = config['B1PASSWORD']
        company.Language = getattr(self.constants, config['LANGUAGE'])
        company.UseTrusted = config['USE_TRUSTED']
        result = company.Connect()
        if result != 0:
            raise Exception("Not connected to COM %s" % result)
        print('Connected to COM')


    def __del__(self):
        if self.company:
            self.company.Disconnect()

    def disconnect(self):
        self.company.Disconnect()
        log = "Close SAPB1 connection for " + self.company.CompanyName
        current_app.logger.info(log)


class MsSqlAdaptor(object):
    """MS SQL cursor object.
    """
    def __init__(self, config):
        self.conn = pymssql.connect(config['SERVER'],
                                    config['DBUSERNAME'],
                                    config['DBPASSWORD'],
                                    config['COMPANYDB'])
        self.cursor = self.conn.cursor(as_dict=True)

    def __del__(self):
        self.conn.close()

    def disconnect(self):
        self.conn.close()
        current_app.logger.info("Close SAPB1 DB connection")

    def execute(self, sql, args=None, **kwargs):
        if args is None:
            pass
        elif isinstance(args, dict):
            pass
        elif isinstance(args, list):
            args = tuple(args)

        if len(kwargs):
            args = kwargs
        self.cursor.execute(sql, args)

    def fetch_all(self, sql, args=None, **kwargs):
        self.execute(sql, args, **kwargs)
        for row in self.cursor:
            item = {}
            for k, v in row.items():
                value = ''
                if isinstance(v, datetime.datetime):
                    value = v.strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(v, unicode):
                    value = v.encode('ascii', 'ignore').decode("utf-8")
                elif isinstance(v, decimal.Decimal):
                    value = str(v)
                elif v is not None:
                    value = v
                item[k] = value
            yield item

    def fetchone(self, sql, args=None, **kwargs):
        self.execute(sql, args, **kwargs)
        return self.cursor.fetchone()


class SAPB1Adaptor(object):
    """SAP B1 Adaptor with functions.
    """

    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """Use the newstyle teardown_appcontext if it's available,
        otherwise fall back to the request context
        """
        if hasattr(app, 'teardown_appcontext'):
            app.teardown_appcontext(self.teardown)
        else:
            app.teardown_request(self.teardown)

    def teardown(self, exception):
        ctx = stack.top
        if hasattr(ctx, '_COM'):
            ctx._COM.disconnect()
        if hasattr(ctx, '_SQL'):
            ctx._SQL.disconnect()

    def info(self):
        """Show the information for the SAP B1 connection.
        """
        com = self.com_adaptor
        data = {
            'company_name': com.company.CompanyName,
            'diapi': current_app.config['DIAPI'],
            'server': current_app.config['SERVER'],
            'company_db': current_app.config['COMPANYDB']
        }
        return data

    @property
    def com_adaptor(self):
        ctx = stack.top
        try:
            return ctx._COM
        except AttributeError:
            ctx._COM = com = SapB1ComAdaptor(current_app.config)
            print(com.company.CompanyName)
            log = "Open SAPB1 connection for " + com.company.CompanyName
            current_app.logger.info(log)
            return com

    @property
    def sql_adaptor(self):
        ctx = stack.top
        try:
            return ctx._SQL
        except AttributeError:
            ctx._SQL = sql = MsSqlAdaptor(current_app.config)
            current_app.logger.info("Open SAPB1 DB connection")
            return sql

    def trimValue(self, value, maxLength):
        """Trim the value.
        """
        if len(value) > maxLength:
            return value[0:maxLength-1]
        return value

    def getOrders(self, num=1, columns=[], params={}):
        """Retrieve orders from SAP B1.
        """
        cols = '*'
        if len(columns) > 0:
            cols = " ,".join(columns)
        ops = {key: '=' if 'op' not in params[key].keys() else params[key]['op'] for key in params.keys()}
        sql = """SELECT top {0} {1} FROM dbo.ORDR""".format(num, cols)
        if len(params) > 0:
            sql = sql + ' WHERE ' + " AND ".join(["{0} {1} %({2})s".format(k, ops[k], k) for k in params.keys()]) + "Order by dbo.ORDR.DocNum DESC"
        args = {key: params[key]['value'] for key in params.keys()}
        print(sql)
        return list(self.sql_adaptor.fetch_all(sql, args=args))

    def getDownPayment(self, num=1, columns=[], params={}):
        """Retreive Down Payments from SAP B1.
        """
        cols = "*"
        cols = '*'
        if len(columns) > 0:
            cols = " ,".join(columns)
        ops = {key: '=' if 'op' not in params[key].keys() else params[key]['op'] for key in params.keys()}
        sql = """SELECT top {0} {1} FROM dbo.ODPI""".format(num, cols)
        if len(params) > 0:
            sql = sql + ' WHERE ' + " AND ".join(["{0} {1} %({2})s".format(k, ops[k], k) for k in params.keys()])
        args = {key: params[key]['value'] for key in params.keys()}
        sql = sql + " ORDER BY DocEntry DESC"
        return list(self.sql_adaptor.fetch_all(sql, args=args))

    def getMainCurrency(self):
        """Retrieve the main currency of the company from SAP B1.
        """
        sql = """SELECT MainCurncy FROM dbo.OADM"""
        return self.sql_adaptor.fetchone(sql)['MainCurncy']

    def getShipCode(self, code): # Code is shipping text from Magento. Return code is U_TrnspCode from [dbo].[@RPC_WEBSHIP_MAP]
        if code == 'FedEx - Priority Overnight':
            return 31  #'FedEx Priority Overnight'
        elif code == 'FedEx - 2nd Day':
            return 30  #'FedEx 2 Day'
        elif code == 'FedEx - International Priority':
            return 27  #'International Priority'
        elif code == 'FedEx - International Economy':
            return 38  #'International Economy'
        elif code == 'FedEx - Ground':
            return 29  #'FedEx Ground'
        elif code == 'USPS - First-Class Mail':
            return 40  #'First Class Mail'
        elif code == 'UPS - Three-Day Select':
            return 14 #'3 Day'
        elif code == 'UPS - Next Day Air Saver':
            return 24  #'Next Day Air Saver'
        elif code == 'UPS - Next Day Air':
            return 2  #'Next Day Air'
        elif code == 'UPS - Second Day Air':
            return 13  #'Second Day Air'
        elif code == 'USPS - Ground':
            return 20  #'USPS Domestic Ground'
        elif code == 'USPS - Priority mail Express International':
            return 21  #'Priorty Mail International Express'
        elif code == 'USPS - Priority Mail International':
            return 27  #'Priority Mail International'
        elif code == 'USPS - First-Class Package International Service':
            return 22  #'First-Class Package International Service'

    def insertBusinessPartner(self, customer):
        """Insert a new business partner
        """
        cardcode_sql = """SELECT MAX(T0.CardCode) AS CardCode FROM OCRD T0 WHERE T0.CARDTYPE = 'C' FOR BROWSE"""
        sql_result = self.sql_adaptor.fetchone(cardcode_sql)
        last_cardcode = sql_result.get('CardCode')
        print('Last CardCode:%s'%last_cardcode)
        next_cardcode = 'C%05d'%(int(last_cardcode.replace('C','').replace('c','')) + 1)
        print('Next CardCode:%s'%next_cardcode)
        com = self.com_adaptor
        busPartner = com.company.GetBusinessObject(com.constants.oBusinessPartners)
        busPartner.CardCode = next_cardcode
        cardname = customer['FirstName'] + ' ' + customer['LastName']
        busPartner.CardName = cardname
        busPartner.GroupCode = '158' #Otros
        busPartner.Phone1 = customer["Phone"]
        busPartner.UserFields.Fields("LicTradNum").Value = customer['RFC']
        busPartner.UserFields.Fields("Phone1").Value = customer['Phone']
        busPartner.UserFields.Fields("E_Mail").Value = customer['Email']
        #BP Address
        address = customer['Address']
        busPartner.Addresses.Add()
        busPartner.Addresses.SetCurrentLine(0)
        busPartner.Addresses.AddressName = "Direccion"
        busPartner.Addresses.Street = address['Street']
        busPartner.Addresses.StreetNo = address['StreetNo']
        busPartner.Addresses.Block = address['Block']
        busPartner.Addresses.County = address['County']
        busPartner.Addresses.City = address['City']
        busPartner.Addresses.State = address['State']
        busPartner.Addresses.ZipCode = address['ZipCode']
        busPartner.Addresses.Country = address['Country']
        #BP Contact
        busPartner.ContactEmployees.Add()
        busPartner.ContactEmployees.SetCurrentLine(0)
        busPartner.ContactEmployees.Name = cardname
        busPartner.ContactEmployees.FirstName = customer['FirstName']
        busPartner.ContactEmployees.LastName = customer['LastName']
        busPartner.ContactEmployees.Phone1 = customer["Phone"]
        busPartner.ContactEmployees.E_Mail = customer["Email"]
        lRetCode = busPartner.Add()
        if lRetCode != 0:
            log = com.company.GetLastErrorDescription()
            current_app.logger.error(log)
            raise Exception(log, customer)
        return {'CardCode':next_cardcode}

    def updateBusinessPartner(self, CardCode, customer):
        """Update business partner by CardCode
        """
        com = self.com_adaptor
        busPartner = com.company.GetBusinessObject(com.constants.oBusinessPartners)
        busPartner.GetByKey(CardCode);
        busPartner.UserFields.Fields("Phone1").Value = customer['Phone']
        busPartner.UserFields.Fields("E_Mail").Value = customer['Email']
        #BP Address
        address = customer['Address']
        busPartner.Addresses.Add()
        busPartner.Addresses.SetCurrentLine(0)
        busPartner.Addresses.AddressName = "Direccion"
        busPartner.Addresses.Street = address['Street']
        busPartner.Addresses.StreetNo = address['StreetNo']
        busPartner.Addresses.Block = address['Block']
        busPartner.Addresses.County = address['County']
        busPartner.Addresses.City = address['City']
        busPartner.Addresses.State = address['State']
        busPartner.Addresses.ZipCode = address['ZipCode']
        lRetCode = busPartner.Update()
        if lRetCode != 0:
            log = com.company.GetLastErrorDescription()
            current_app.logger.error(log)
            raise Exception(log)
        return {'CardCode':CardCode}

    def getBusinessPartners(self):
        pass

    def getContacts(self, num=1, columns=[], cardCode=None, contact={}):
        """Retrieve contacts under a business partner by CardCode from SAP B1.
        """
        cols = '*'
        if len(columns) > 0:
            cols = " ,".join(columns)

        sql = """SELECT top {0} {1} FROM dbo.OCPR""".format(num, cols)
        if contact:
            params = dict({(k, 'null' if v is None else v) for k, v in contact.items()})
        else:
            params = {}
        params['cardcode'] = cardCode
        sql = sql + ' WHERE ' + " AND ".join(["{0} = %({1})s".format(k, k) for k in params.keys()])
        return list(self.sql_adaptor.fetch_all(sql, **params))

    def insertContact(self, cardCode, contact):
        """Insert a new contact into a business partner by CardCode.
        """
        com = self.com_adaptor
        busPartner = com.company.GetBusinessObject(com.constants.oBusinessPartners)
        busPartner.GetByKey(cardCode)
        current = busPartner.ContactEmployees.Count
        if busPartner.ContactEmployees.InternalCode == 0:
            nextLine = 0
        else:
            nextLine = current
        busPartner.ContactEmployees.Add()
        busPartner.ContactEmployees.SetCurrentLine(nextLine)
        name = contact['FirstName'] + ' ' + contact['LastName']
        name = name[0:36] + ' ' + str(time())
        busPartner.ContactEmployees.Name = name
        busPartner.ContactEmployees.FirstName = contact['FirstName']
        busPartner.ContactEmployees.LastName = contact['LastName']
        busPartner.ContactEmployees.Phone1 = contact["Tel1"]
        busPartner.ContactEmployees.E_Mail = contact["E_MailL"]
        address = contact['Address']
        busPartner.ContactEmployees.Address = self.trimValue(address, 100)
        lRetCode = busPartner.Update()
        if lRetCode != 0:
            log = self.company.GetLastErrorDescription()
            current_app.logger.error(log)
            raise Exception(log)

        cntct = {
            "name": name,
            "FirstName": contact['FirstName'],
            "LastName": contact['LastName'],
            "E_MailL": contact["E_MailL"]
        }
        contacts = self.getContacts(num=1, columns=['cntctcode'], cardCode=cardCode, contact=cntct)
        contactCode = contacts[0]['cntctcode']
        return contactCode

    def getContactPersonCode(self, order):
        """Retrieve ContactPersonCode by an order.
        """
        contact = {
            'FirstName': order['billto_firstname'],
            'LastName': order['billto_lastname'],
            'E_MailL': order['billto_email']
        }
        contacts = self.getContacts(num=1, columns=['cntctcode'], cardCode=order['card_code'], contact=contact)
        contactCode = None
        if len(contacts) == 1:
            contactCode = contacts[0]['cntctcode']
        if contactCode is None:
            address = order['billto_address'] + ', ' \
                      + order['billto_city'] + ', ' \
                      + order['billto_state'] + ' ' \
                      + order['billto_zipcode'] + ', ' \
                      + order['billto_country']
            contact['Address'] = self.trimValue(address, 100)
            contact['Tel1'] = order['billto_telephone']
            contactCode = self.insertContact(order['card_code'], contact)
        return contactCode

    def getExpnsCode(self, expnsName):
        """Retrieve expnsCode by expnsName.
        """
        sql = """SELECT ExpnsCode FROM dbo.OEXD WHERE ExpnsName = %s"""
        cursor = self.sql_adaptor.cursor
        cursor.execute(sql, expnsName)
        expnsCode = cursor.fetchone()['ExpnsCode']
        return expnsCode

    def getTrnspCode(self, trnspName):
        """Retrieve TrnspCode by trnspName.  """
        sql = """SELECT [dbo].[@RPC_WEBSHIP_MAP].U_Service FROM [dbo].[@RPC_WEBSHIP_MAP] WHERE U_MagentoCode = %s"""
        print(self.sql_adaptor.fetchone(sql, trnspName))
        return self.sql_adaptor.fetchone(sql, trnspName)['U_Service']

    def getExpnsNames(self):
        """Retrieve expnsNames. """
        sql = """SELECT ExpnsName FROM dbo.OEXD"""
        return list(self.sql_adaptor.fetch_all(sql))

    def getTrnspNames(self):
        """Retrieve TrnspNames.
        """
        sql = """SELECT TrnspName FROM dbo.OSHP"""
        return list(self.sql_adaptor.fetch_all(sql))

    def getPayMethCods(self):
        sql = """SELECT PayMethCod from opym"""
        return list(self.sql_adaptor.fetch_all(sql))

    def getTaxCodes(self):
        sql = """SELECT Code, Name, Rate from osta"""
        return list(self.sql_adaptor.fetch_all(sql))

    def getUSDRate(self):
        sql = """SELECT Rate from ORTT where RateDate='{0}'""".format(strftime("%Y-%m-%d"))
        return list(self.sql_adaptor.fetch_all(sql))

    def insertOrder(self, o):
        """Insert an order into SAP B1.
        """
        com = self.com_adaptor
        order = com.company.GetBusinessObject(com.constants.oOrders)
        order.DocDate = o['order_date']
        order.DocDueDate = o['doc_due_date']
        order.CardCode = 'C105212'
        #order.NumAtCard = str(o['num_at_card'])
        # UDF for Magento Web Order ID
        order.UserFields.Fields("U_OrderSource").Value = 'Web Order'
        order.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
        order.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
        order.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
        order.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
        order.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
        order.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
        order.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
        order.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
        order.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

        if o['cc_type'] == 'MASTERCARD':
            order.UserFields.Fields("U_web_cc_type").Value = 'MC'
        elif o['cc_type'] == 'VISA':
            order.UserFields.Fields("U_web_cc_type").Value = 'VISA'
        elif o['cc_type'] == 'AMERICAN EXPRESS':
            order.UserFields.Fields("U_web_cc_type").Value = 'AMEX'
        elif o['cc_type'] == 'DISCOVER':
            order.UserFields.Fields("U_web_cc_type").Value = 'DC'

        if o['user_id']:
            order.UserFields.Fields("U_WebCustomerID").Value = str(o['user_id'])

        if 'order_shipping_cost' in o.keys():
            order.Expenses.ExpenseCode = 1
            order.Expenses.LineTotal = o['order_shipping_cost']
            order.Expenses.TaxCode = 'FLEX'

        if 'discount_percent' in o.keys():
            order.DiscountPercent = o['discount_percent']

        if 'transport_name' in o.keys():
            code = self.getShipCode(o['transport_name'])
            order.TransportationCode = code


        # Set Payment Method
        if 'payment_method' in o.keys():
            order.PaymentMethod = o['payment_method']


        ## Set bill to address properties
        order.AddressExtension.BillToCity = o['billto_city']
        order.AddressExtension.BillToCountry = o['billto_country']
        order.AddressExtension.BillToState = o['billto_state']
        order.AddressExtension.BillToStreet = o['billto_address']
        order.AddressExtension.BillToZipCode = o['billto_zipcode']

        ## Set ship to address properties
        order.AddressExtension.ShipToCity = o['shipto_city']
        order.AddressExtension.ShipToCountry = o['shipto_country']
        order.AddressExtension.ShipToState = o['shipto_state']
        order.AddressExtension.ShipToStreet = o['shipto_address']
        order.AddressExtension.ShipToZipCode = o['shipto_zipcode']

        # Set Comments
        if 'comments' in o.keys():
            order.Comments = o['comments']


        #loop through items in order
        i = 0
        for item in o['items']:
            order.Lines.SetCurrentLine(i)
            order.Lines.ItemCode = item['itemcode']
            order.Lines.TaxCode = 'FLEX'
            order.Lines.Quantity = float(item['quantity'])
            print(item['price'])
            #order.lines.UnitPrice = item['price']
            if item['discount_percent']:
                order.Lines.UnitPrice = float(item['price'])
                order.Lines.DiscountPercent = float(item['discount_percent'])
            i = i + 1
            order.Lines.Add()
        if o['order_tax'] != '0.00':
            order.Lines.SetCurrentLine(i)
            order.Lines.ItemCode = 'SALESTAX'
            order.Lines.Quantity = 1
            order.Lines.TaxCode = 'FLEX'
            order.Lines.UnitPrice = o['order_tax']
            order.Lines.Add()
        lRetCode = order.Add()
        if lRetCode != 0:
            error = str(self.com_adaptor.company.GetLastError())
            current_app.logger.error(error)
            #Send email error
            fromaddr = 'sapb1@riflepaperco.com'
            toaddrs  = 'helpdesk@riflepaperco.com'
            msg = "\r\n".join([
              "From: sapb1@riflepaperco.com",
              "To: helpdesk@riflepaperco.com",
              "Subject: SAPB1Int: Error adding Sales Order " + o['U_WebOrderId'],
              "",
              "Error adding Sales Order (ORDR) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually import order."
              ])
            username = 'apps@riflepaperco.com'
            password = 'pass'
            server = smtplib.SMTP('smtp.gmail.com:587')
            server.starttls()
            server.login(username,password)
            server.sendmail(fromaddr, toaddrs, msg)
            server.quit()
            raise Exception(error, o['U_WebOrderId'])

        params = None
        params = {'U_WebOrderId': {'value': str(o['U_WebOrderId'])}}
        orders = self.getOrders(num=1, columns=['DocEntry', 'DocTotal', 'DocNum'], params=params)
        find_order_sql = """
                            SELECT top 1 dbo.ORDR.DocEntry, dbo.ORDR.DocNum, dbo.ORDR.DocTotal
                            FROM dbo.ORDR
                            WHERE dbo.ORDR.U_WebOrderId='{0}'
                            ORDER BY dbo.ORDR.DocNum DESC
                        """.format(o['U_WebOrderId'])

        latestOrder = self.getLatestOrder(find_order_sql)
        print(latestOrder)
        orderDocEntry = latestOrder[0]['DocEntry']
        orderDocTotal = latestOrder[0]['DocTotal']
        orderDocNum = latestOrder[0]['DocNum']

        if o['order_total'] > 0:

            if o['giftcard'] and o['giftcard_amount'] < o['order_total'] and o['giftcard_amount'] != '0.0000':
                print("DOUBLE DOWN PAYMENT")
                cashDownPayment = com.company.GetBusinessObject(com.constants.oDownPayments)
                cashDownPayment.DownPaymentType = com.constants.dptInvoice
                cashDownPayment.DocDate = o['order_date']
                cashDownPayment.DocDueDate = o['doc_due_date']
                cashDownPayment.CardCode = 'C105212'
                cashDownPayment.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
                cashDownPayment.UserFields.Fields("U_OrderSource").Value = 'Web Order'
                cashDownPayment.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
                cashDownPayment.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
                cashDownPayment.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
                cashDownPayment.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
                cashDownPayment.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
                cashDownPayment.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
                cashDownPayment.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
                cashDownPayment.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

                i = 0
                for item in o['items']:
                    cashDownPayment.Lines.SetCurrentLine(i)
                    cashDownPayment.Lines.ItemCode = item['itemcode']
                    cashDownPayment.Lines.Quantity = float(item['quantity'])
                    if item.get('price'):
                        cashDownPayment.Lines.UnitPrice = float(item['price'])
                    i = i + 1
                    cashDownPayment.Lines.Add()

                cashDownPayment.DocTotal = (float(orderDocTotal) - float(o['giftcard_amount']))
                lRetCode1 = cashDownPayment.Add()

                if lRetCode1 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding DPI " + o['U_WebOrderId'],
                      "",
                      "Error adding Downpayment Invoice (ODPI) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create DPI."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'], o['items'])

                find_latest_dpi="""
                    SELECT dbo.ODPI.DocEntry, dbo.ODPI.DocTotal, dbo.ODPI.DocDate
                    FROM dbo.ODPI
                    WHERE dbo.ODPI.U_WebOrderId = '{0}' and dbo.ODPI.DocStatus='O'
                    ORDER BY dbo.ODPI.DocNum DESC
                """.format(o['U_WebOrderId'])

                downpayments = self.getLineNum(find_latest_dpi)

                downPaymentDocEntry = downpayments[0]['DocEntry']
                downPaymentDocTotal = downpayments[0]['DocTotal']
                downPaymentDocDate = downpayments[0]['DocDate']
                if downPaymentDocEntry:
                    link_downpayment_sql= """UPDATE dbo.DPI1
                                                SET dbo.DPI1.BaseRef = q.DocNum, dbo.DPI1.BaseType = 17, dbo.DPI1.BaseEntry = q.DocEntry
                                                FROM dbo.ORDR q
                                                WHERE dbo.DPI1.DocEntry = '{0}'
                                                AND q.DocEntry = '{1}'
                                            """.format(downPaymentDocEntry,orderDocEntry)
                    cursor = self.sql_adaptor.cursor
                    cursor.execute(link_downpayment_sql)
                    self.sql_adaptor.conn.commit()

                cashIncomingPayments = com.company.GetBusinessObject(com.constants.oIncomingPayments)
                cashIncomingPayments.Invoices.DocEntry = downPaymentDocEntry
                cashIncomingPayments.Invoices.InvoiceType = com.constants.it_DownPayment
                cashIncomingPayments.Invoices.SumApplied = downPaymentDocTotal
                cashIncomingPayments.TransferSum = downPaymentDocTotal
                cashIncomingPayments.TransferAccount = '_SYS00000000166'
                cashIncomingPayments.TransferReference = o['U_WebOrderId']
                cashIncomingPayments.CardCode = 'C105212'
                cashIncomingPayments.DocDate = downPaymentDocDate
                cashIncomingPayments.TransferDate = downPaymentDocDate
                lRetCode3 = cashIncomingPayments.Add()
                if lRetCode3 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding Payment " + o['U_WebOrderId'],
                      "",
                      "Error adding Incoming Payment (ORCT) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create payment."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'])


                gcDownPayment = com.company.GetBusinessObject(com.constants.oDownPayments)
                gcDownPayment.DownPaymentType = com.constants.dptInvoice
                gcDownPayment.DocDate = o['order_date']
                gcDownPayment.DocDueDate = o['doc_due_date']
                gcDownPayment.CardCode = 'C105212'
                gcDownPayment.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
                gcDownPayment.UserFields.Fields("U_OrderSource").Value = 'Web Order'
                gcDownPayment.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
                gcDownPayment.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
                gcDownPayment.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
                gcDownPayment.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
                gcDownPayment.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
                gcDownPayment.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
                gcDownPayment.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
                gcDownPayment.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

                i = 0
                for item in o['items']:
                    gcDownPayment.Lines.SetCurrentLine(i)
                    gcDownPayment.Lines.ItemCode = item['itemcode']
                    gcDownPayment.Lines.Quantity = float(item['quantity'])
                    if item.get('price'):
                        gcDownPayment.Lines.UnitPrice = float(item['price'])
                    i = i + 1
                    gcDownPayment.Lines.Add()


                gcDownPayment.DocTotal = float(o['giftcard_amount'])
                lRetCode2 = gcDownPayment.Add()

                if lRetCode2 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding Payment " + o['U_WebOrderId'],
                      "",
                      "Error adding Incoming Payment (ORCT) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create payment."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'], "second gift card error")

                #Linking Down Payment with Sales Order
                find_latest_dpi="""
                    SELECT dbo.ODPI.DocEntry, dbo.ODPI.DocTotal, dbo.ODPI.DocDate
                    FROM dbo.ODPI
                    WHERE dbo.ODPI.U_WebOrderId = '{0}' and dbo.ODPI.DocStatus='O'
                    ORDER BY dbo.ODPI.DocNum DESC
                """.format(o['U_WebOrderId'])

                downpayments1 = self.getLineNum(find_latest_dpi)
                downPaymentDocEntry1 = downpayments1[0]['DocEntry']
                downPaymentDocTotal1 = downpayments1[0]['DocTotal']
                downPaymentDocDate1 = downpayments1[0]['DocDate']
                if downPaymentDocEntry1:
                    link_downpayment_sql= """UPDATE dbo.DPI1
                                                SET dbo.DPI1.BaseRef = q.DocNum, dbo.DPI1.BaseType = 17, dbo.DPI1.BaseEntry = q.DocEntry
                                                FROM dbo.ORDR q
                                                WHERE dbo.DPI1.DocEntry = '{0}'
                                                AND q.DocEntry = '{1}'
                                            """.format(downPaymentDocEntry1,orderDocEntry)
                    cursor = self.sql_adaptor.cursor
                    cursor.execute(link_downpayment_sql)
                    self.sql_adaptor.conn.commit()

                gcIncomingPayments = com.company.GetBusinessObject(com.constants.oIncomingPayments)
                gcIncomingPayments.Invoices.DocEntry = downPaymentDocEntry1
                gcIncomingPayments.Invoices.InvoiceType = com.constants.it_DownPayment
                gcIncomingPayments.Invoices.SumApplied = downPaymentDocTotal1
                gcIncomingPayments.TransferSum = downPaymentDocTotal1
                gcIncomingPayments.TransferAccount = '_SYS00000000517'
                gcIncomingPayments.TransferReference = o['U_WebOrderId']
                gcIncomingPayments.CardCode = 'C105212'
                gcIncomingPayments.DocDate = downPaymentDocDate
                gcIncomingPayments.TransferDate = downPaymentDocDate1
                lRetCode4 = gcIncomingPayments.Add()
                if lRetCode4 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding Payment " + o['U_WebOrderId'],
                      "",
                      "Error adding Incoming Payment (ORCT) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create payment."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'])


            elif o['giftcard'] and o['giftcard_amount'] >= o['order_total']:
                downPayment = com.company.GetBusinessObject(com.constants.oDownPayments)
                downPayment.DownPaymentType = com.constants.dptInvoice
                downPayment.DocDate = o['order_date']
                downPayment.DocDueDate = o['doc_due_date']
                downPayment.CardCode = 'C105212'
                #order.NumAtCard = str(o['num_at_card'])
                # User Field
                downPayment.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
                downPayment.UserFields.Fields("U_OrderSource").Value = 'Web Order'
                downPayment.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
                downPayment.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
                downPayment.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
                downPayment.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
                downPayment.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
                downPayment.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
                downPayment.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
                downPayment.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

                # Set Comments
                if 'comments' in o.keys():
                    downPayment.Comments = o['comments']

                i = 0
                for item in o['items']:
                    downPayment.Lines.SetCurrentLine(i)
                # downPayment.Lines.BaseLine = i
                # downPayment.Lines.BaseEntry = orderDocEntry
                # downPayment.Lines.BaseType = 17
                    downPayment.Lines.ItemCode = item['itemcode']
                    downPayment.Lines.Quantity = float(item['quantity'])
                    if item.get('price'):
                        downPayment.Lines.UnitPrice = float(item['price'])
                    i = i + 1
                    downPayment.Lines.Add()

                downPayment.DocTotal = orderDocTotal
                lRetCode1 = downPayment.Add()
                if lRetCode1 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding DPI " + o['U_WebOrderId'],
                      "",
                      "Error adding Downpayment Invoice (ODPI) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create DPI."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'])
                #Linking Down Payment with Sales Order
                downpayments = self.getDownPayment(num=1, columns=['DocEntry', 'DocTotal', 'DocDate'], params=params)
                downPaymentDocEntry = downpayments[0]['DocEntry']
                downPaymentDocTotal = downpayments[0]['DocTotal']
                downPaymentDocDate = downpayments[0]['DocDate']
                if orderDocEntry:
                    link_downpayment_sql= """UPDATE dbo.DPI1
                                                SET dbo.DPI1.BaseRef = q.DocNum, dbo.DPI1.BaseType = 17, dbo.DPI1.BaseEntry = q.DocEntry
                                                FROM dbo.ORDR q
                                                WHERE dbo.DPI1.DocEntry = '{0}'
                                                AND q.DocEntry = '{1}'
                                            """.format(downPaymentDocEntry,orderDocEntry)
                    cursor = self.sql_adaptor.cursor
                    cursor.execute(link_downpayment_sql)
                    self.sql_adaptor.conn.commit()

                incomingPayments = com.company.GetBusinessObject(com.constants.oIncomingPayments)
                incomingPayments = com.company.GetBusinessObject(com.constants.oIncomingPayments)
                incomingPayments.Invoices.DocEntry = downPaymentDocEntry
                incomingPayments.Invoices.InvoiceType = com.constants.it_DownPayment
                incomingPayments.Invoices.SumApplied = downPaymentDocTotal
                incomingPayments.CardCode = 'C105212'
                #incomingPayments.Comments = 'Created by Integration'
                incomingPayments.TransferAccount = '_SYS00000000517'
                incomingPayments.TransferReference = o['U_WebOrderId']
                incomingPayments.DocDate = downPaymentDocDate
                incomingPayments.TransferDate = downPaymentDocDate
                incomingPayments.TransferSum = downPaymentDocTotal
                lRetCode2 = incomingPayments.Add()
                if lRetCode2 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding Payment " + o['U_WebOrderId'],
                      "",
                      "Error adding Incoming Payment (ORCT) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create payment."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'])

            else:
                print("GIFTCARD NO")
                downPayment = com.company.GetBusinessObject(com.constants.oDownPayments)
                downPayment.DownPaymentType = com.constants.dptInvoice
                downPayment.DocDate = o['order_date']
                downPayment.DocDueDate = o['doc_due_date']
                downPayment.CardCode = 'C105212'
                #order.NumAtCard = str(o['num_at_card'])
                # User Field
                downPayment.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
                downPayment.UserFields.Fields("U_OrderSource").Value = 'Web Order'
                downPayment.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
                downPayment.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
                downPayment.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
                downPayment.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
                downPayment.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
                downPayment.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
                downPayment.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
                downPayment.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

                # Set Comments
                if 'comments' in o.keys():
                    downPayment.Comments = o['comments']

                i = 0
                for item in o['items']:
                    downPayment.Lines.SetCurrentLine(i)
                # downPayment.Lines.BaseLine = i
                # downPayment.Lines.BaseEntry = orderDocEntry
                # downPayment.Lines.BaseType = 17
                    downPayment.Lines.ItemCode = item['itemcode']
                    downPayment.Lines.Quantity = float(item['quantity'])
                    if item.get('price'):
                        downPayment.Lines.UnitPrice = float(item['price'])
                    i = i + 1
                    downPayment.Lines.Add()

                downPayment.DocTotal = orderDocTotal
                lRetCode1 = downPayment.Add()
                if lRetCode1 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding DPI " + o['U_WebOrderId'],
                      "",
                      "Error adding Downpayment Invoice (ODPI) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create DPI."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'])
                #Linking Down Payment with Sales Order
                downpayments = self.getDownPayment(num=1, columns=['DocEntry', 'DocTotal', 'DocDate'], params=params)
                downPaymentDocEntry = downpayments[0]['DocEntry']
                downPaymentDocTotal = downpayments[0]['DocTotal']
                downPaymentDocDate = downpayments[0]['DocDate']
                if downPaymentDocEntry:
                    link_downpayment_sql= """UPDATE dbo.DPI1
                                                SET dbo.DPI1.BaseRef = q.DocNum, dbo.DPI1.BaseType = 17, dbo.DPI1.BaseEntry = q.DocEntry
                                                FROM dbo.ORDR q
                                                WHERE dbo.DPI1.DocEntry = '{0}'
                                                AND q.DocEntry = '{1}'
                                            """.format(downPaymentDocEntry,orderDocEntry)
                    cursor = self.sql_adaptor.cursor
                    cursor.execute(link_downpayment_sql)
                    self.sql_adaptor.conn.commit()

                incomingPayments = com.company.GetBusinessObject(com.constants.oIncomingPayments)
                incomingPayments.Invoices.DocEntry = downPaymentDocEntry
                incomingPayments.Invoices.InvoiceType = com.constants.it_DownPayment
                incomingPayments.Invoices.SumApplied = downPaymentDocTotal
                incomingPayments.CardCode = 'C105212'
                #incomingPayments.Comments = 'Created by Integration'
                incomingPayments.TransferAccount = '_SYS00000000166'
                incomingPayments.TransferReference = o['U_WebOrderId']
                incomingPayments.DocDate = downPaymentDocDate
                incomingPayments.TransferDate = downPaymentDocDate
                incomingPayments.TransferSum = downPaymentDocTotal
                lRetCode2 = incomingPayments.Add()
                if lRetCode2 != 0:
                    error = str(self.com_adaptor.company.GetLastError())
                    current_app.logger.error(error)
                    #Send email error
                    fromaddr = 'sapb1@riflepaperco.com'
                    toaddrs  = 'helpdesk@riflepaperco.com'
                    msg = "\r\n".join([
                      "From: sapb1@riflepaperco.com",
                      "To: helpdesk@riflepaperco.com",
                      "Subject: SAPB1Int: Error adding Payment " + o['U_WebOrderId'],
                      "",
                      "Error adding Incoming Payment (ORCT) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually create payment."
                      ])
                    username = 'apps@riflepaperco.com'
                    password = 'pass'
                    server = smtplib.SMTP('smtp.gmail.com:587')
                    server.starttls()
                    server.login(username,password)
                    server.sendmail(fromaddr, toaddrs, msg)
                    server.quit()
                    raise Exception(error, o['U_WebOrderId'])

        return orderDocEntry

    def insertQuotation(self, q):
        """Create a quotation into SAP B1.
        """
        com = self.com_adaptor
        quotation = com.company.GetBusinessObject(com.constants.oQuotations)
        quotation.DocDueDate = q['doc_due_date']
        quotation.CardCode = q['card_code']
        quotation.NumAtCard = str(q['num_at_card'])

        i = 0
        for item in q['items']:
            quotation.Lines.Add()
            quotation.Lines.SetCurrentLine(i)
            quotation.Lines.ItemCode = item['itemcode']
            quotation.Lines.Quantity = float(item['quantity'])
            if item.get('price'):
                quotation.Lines.UnitPrice = float(item['price'])
            i = i + 1

        lRetQCode = quotation.Add()
        if lRetQCode != 0:
            error = str(self.com_adaptor.company.GetLastError())
            current_app.logger.error(error)
            raise Exception(error)

        quotation_sql = """SELECT top(1) DocEntry FROM dbo.OQUT
                            WHERE NumAtCard = %s"""
        sqlresult = self.sql_adaptor.fetchone(quotation_sql, q['num_at_card'])
        quotationDocEntry = sqlresult['DocEntry']
        return quotationDocEntry

    def cancelOrder(self, o):
        """Cancel an order in SAP B1.
        """
        com = self.com_adaptor
        order = com.company.GetBusinessObject(com.constants.oOrders)
        params = None
        if 'fe_order_id_udf' in o.keys():
            params = {o['fe_order_id_udf']: {'value': str(o['fe_order_id'])}}
        else:
            params = {'NumAtCard': {'value': str(o['fe_order_id'])}}
        orders = self.getOrders(num=1, columns=['DocEntry'], params=params)
        if orders:
            boOrderId = orders[0]['DocEntry']
            order.GetByKey(boOrderId)
            lRetCode = order.Cancel()
            if lRetCode != 0:
                error = str(self.company.GetLastError())
                self.logger.error(error)
                raise Exception(error)
            else:
                return boOrderId
        else:
            raise Exception("Order {0} is not found.".format(o['fe_order_id']))

    def _getShipmentItems(self, shipmentId, columns=[]):
        """Retrieve line items for each shipment(delivery) from SAP B1.
        """
        cols = "*"
        if len(columns) > 0:
            cols = " ,".join(columns)
        sql = """SELECT {0} FROM dbo.DLN1""".format(cols)
        params = {
            'DocEntry': shipmentId
        }
        if len(params) > 0:
            sql = sql + ' WHERE ' + " AND ".join(["{0} = %({1})s".format(k, k) for k in params.keys()])
        return list(self.sql_adaptor.fetch_all(sql, params))

#    def getShipments(self, num=100, columns=[], params={}, itemColumns=[]):
#        """Retrieve shipments(deliveries) from SAP B1.
#        """
#        cols = '*'
#        if 'DocEntry' not in columns:
#            columns.append('DocEntry')
#        if len(columns) > 0:
#            cols = " ,".join(columns)
#        ops = {key: '=' if 'op' not in params[key].keys() else params[key]['op'] for key in params.keys()}
#        sql = """SELECT top {0} {1} FROM dbo.ODLN""".format(num, cols)
#        if len(params) > 0:
#            sql = sql + ' WHERE ' + " AND ".join(["{0} {1} %({2})s".format(k, ops[k], k) for k in params.keys()])#
#
#        p = {key: v['value'] for key, v in params.keys()}
#        shipments = list(self.sql_adaptor.fetch_all(sql, p))
#        for shipment in shipments:
#            shipmentId = shipment['DocEntry']
#            shipment['items'] = self._getShipmentItems(shipmentId, itemColumns)
#        return shipments

    def getShipments(self, num=1, columns=[], params={}):
        """Retrieve orders from SAP B1.
        """
        cols = '*'
        if len(columns) > 0:
            cols = " ,".join(columns)
        ops = {key: '=' if 'op' not in params[key].keys() else params[key]['op'] for key in params.keys()}
        sql = """SELECT top {0} {1} FROM dbo.ODLN""".format(num, cols)
        if len(params) > 0:
            sql = sql + ' WHERE ' + " AND ".join(["{0} {1} %({2})s".format(k, ops[k], k) for k in params.keys()])
        args = {key: params[key]['value'] for key in params.keys()}
        print(sql)
        return list(self.sql_adaptor.fetch_all(sql, args=args))

    def getLineNum(self, sql):
        return list(self.sql_adaptor.fetch_all(sql))

    def getLatestOrder(self, sql):
        return list(self.sql_adaptor.fetch_all(sql))

    def getOrderShipInfo(self, num=1, columns=[], params={}):
        """Retrieve order shipping info from SAP B1.
        """
        cols = '*'
        if len(columns) > 0:
            cols = " ,".join(columns)
        ops = {key: '=' if 'op' not in params[key].keys() else params[key]['op'] for key in params.keys()}
        sql = """SELECT top {0} {1} FROM dbo.RDR3""".format(num, cols)
        if len(params) > 0:
            sql = sql + ' WHERE ' + " AND ".join(["{0} {1} %({2})s".format(k, ops[k], k) for k in params.keys()])
        args = {key: params[key]['value'] for key in params.keys()}
        print(sql)
        return list(self.sql_adaptor.fetch_all(sql, args=args))

    def insertShipment(self, o):
        """Insert shipments into SAP B1.
        """
        com = self.com_adaptor
        delivery =  com.company.GetBusinessObject(com.constants.oDeliveryNotes)

        params = None
        print(str(o['U_WebOrderId']))
        params = {'U_WebOrderId': {'value': str(o['U_WebOrderId'])}}
        # orders = self.getOrders(num=1, columns=['DocEntry', 'DocTotal', 'DocNum'], params=params)
        find_order_sql = """
                            SELECT top 1 dbo.ORDR.DocEntry, dbo.ORDR.DocNum, dbo.ORDR.DocTotal
                            FROM dbo.ORDR
                            WHERE dbo.ORDR.U_WebOrderId='{0}' and dbo.ORDR.DocStatus = 'O'
                            ORDER BY dbo.ORDR.DocNum DESC
                        """.format(o['U_WebOrderId'])

        latestOrder = self.getLatestOrder(find_order_sql)

        orderDocEntry = latestOrder[0]['DocEntry']
        orderDocTotal = latestOrder[0]['DocTotal']
        orderDocNum = latestOrder[0]['DocNum']


        delivery.DocDate = o['order_date']
        delivery.DocDueDate = o['doc_due_date']
        delivery.CardCode = 'C105212'
        #order.NumAtCard = str(o['num_at_card'])
        # UDF for Magento Web Order ID
        # delivery.UserFields.Fields("U_OrderSource").Value = 'Web Order'
        # delivery.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
        # delivery.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
        # delivery.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
        # delivery.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
        # delivery.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
        # delivery.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
        # delivery.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
        # delivery.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
        # delivery.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

        # if o['cc_type'] == 'MASTERCARD':
        #     delivery.UserFields.Fields("U_web_cc_type").Value = 'MC'
        # elif o['cc_type'] == 'VISA':
        #     delivery.UserFields.Fields("U_web_cc_type").Value = 'VISA'
        # elif o['cc_type'] == 'AMERICAN EXPRESS':
        #     delivery.UserFields.Fields("U_web_cc_type").Value = 'AMEX'
        # elif o['cc_type'] == 'DISCOVER':
        #     delivery.UserFields.Fields("U_web_cc_type").Value = 'DC'

        # if o['user_id']:
        #     delivery.UserFields.Fields("U_WebCustomerID").Value = str(o['user_id'])

        if 'order_shipping_cost' in o.keys():
            find_ship_sql = """
            SELECT dbo.RDR3.LineTotal
            FROM dbo.ORDR inner join dbo.RDR3 ON dbo.ORDR.DocEntry = dbo.RDR3.DocEntry
            WHERE dbo.ORDR.DocNum={0} and dbo.RDR3.Status='O'
            ORDER BY dbo.ORDR.DocNum DESC
            """.format(orderDocNum)


            shiptotal = self.getLineNum(find_ship_sql)
            if shiptotal:
                delivery.Expenses.ExpenseCode = 1
                delivery.Expenses.LineTotal = shiptotal[0]['LineTotal']
                delivery.Expenses.TaxCode = 'FLEX'
                delivery.Expenses.BaseDocEntry = orderDocEntry
                delivery.Expenses.BaseDocLine = 0
                delivery.Expenses.BaseDocType = 17
                # delivery.Expenses.BaseDocumentReference = orderDocNum


        # # if 'discount_percent' in o.keys():
        # #     delivery.DiscountPercent = o['discount_percent']

        # # Set Shipping Type
        # if 'transport_name' in o.keys():
        #     code = self.getShipCode(o['transport_name'])
        #     delivery.TransportationCode = code

        # # Set Payment Method
        # if 'payment_method' in o.keys():
        #     delivery.PaymentMethod = o['payment_method']

        # ## Set bill to address properties
        # delivery.AddressExtension.BillToCity = o['billto_city']
        # delivery.AddressExtension.BillToCountry = o['billto_country']
        # delivery.AddressExtension.BillToState = o['billto_state']
        # delivery.AddressExtension.BillToStreet = o['billto_address']
        # delivery.AddressExtension.BillToZipCode = o['billto_zipcode']

        # ## Set ship to address properties
        # delivery.AddressExtension.ShipToCity = o['shipto_city']
        # delivery.AddressExtension.ShipToCountry = o['shipto_country']
        # delivery.AddressExtension.ShipToState = o['shipto_state']
        # delivery.AddressExtension.ShipToStreet = o['shipto_address']
        # delivery.AddressExtension.ShipToZipCode = o['shipto_zipcode']

        # # Set Comments
        # if 'comments' in o.keys():
        #     delivery.Comments = o['comments']

        # paramsOrderShip = {'DocEntry': {'value': str(orderDocEntry)}}
        # orderShipInfo = self.getOrderShipInfo(num=1, columns=['DocEntry', 'LineTotal', 'ObjType', 'TaxCode', 'ExpnsCode', 'LineNum'], params=paramsOrderShip)

        testarr = []
        i = 0
        for item in o['items']:
            # delivery.Lines.SetCurrentLine(i)
            delivery.Lines.Quantity = float(item['quantity'])
            #delivery.Lines.BaseEntry = float(orderDocEntry)
            delivery.Lines.TaxCode = 'FLEX'

            # if item.get('price'):
            #     delivery.Lines.UnitPrice = float(item['price'])

            find_ordr_linenum_sql="""SELECT dbo.RDR1.LineNum, dbo.RDR1.ItemCode
                                    FROM dbo.ORDR INNER JOIN dbo.RDR1 ON dbo.ORDR.DocEntry = dbo.RDR1.DocEntry
                                    WHERE dbo.ORDR.DocNum = '{0}' and dbo.RDR1.ItemCode = '{1}'
                                    """.format(orderDocNum, item['itemcode'])

            lineNum = self.getLineNum(find_ordr_linenum_sql)
            print(lineNum)
            test = int(lineNum[0]['LineNum'])
            delivery.Lines.ItemCode = lineNum[0]['ItemCode']
            print(test)
            testarr.append(test)
            delivery.Lines.BaseLine = test
            #delivery.Lines.BaseRef = orderDocNum
            delivery.Lines.BaseType = 17
            delivery.Lines.BaseEntry = int(orderDocEntry)
            delivery.Lines.Add()
            i = i + 1
        if o['order_tax'] != '0.00':
            find_ordr_linenum_sql="""SELECT dbo.RDR1.LineNum, dbo.RDR1.ItemCode
                                    FROM dbo.ORDR INNER JOIN dbo.RDR1 ON dbo.ORDR.DocEntry = dbo.RDR1.DocEntry
                                    WHERE dbo.ORDR.DocNum = '{0}' and dbo.RDR1.ItemCode = '{1}' and dbo.RDR1.LineStatus = 'O'
                                    """.format(orderDocNum, "SALESTAX")
            lineNum = self.getLineNum(find_ordr_linenum_sql)
            if lineNum:
                # delivery.Lines.SetCurrentLine(i)
                delivery.Lines.ItemCode = lineNum[0]['ItemCode']
                # delivery.Lines.TaxCode = 'FLEX'
                #delivery.Lines.Quantity = 1
                # delivery.Lines.UnitPrice = o['order_tax']
                test = int(lineNum[0]['LineNum'])
                findItem = lineNum[0]['ItemCode']
                delivery.Lines.BaseLine = test
                #delivery.Lines.BaseRef = orderDocNum
                delivery.Lines.BaseType = 17
                delivery.Lines.BaseEntry = int(orderDocEntry)
                delivery.Lines.Add()
                testarr.append(test)
        lRetCode = delivery.Add()
        if lRetCode != 0:
            error = str(self.com_adaptor.company.GetLastError())
            current_app.logger.error(error)
            #Send email error
            fromaddr = 'sapb1@riflepaperco.com'
            toaddrs  = 'helpdesk@riflepaperco.com'
            msg = "\r\n".join([
              "From: sapb1@riflepaperco.com",
              "To: helpdesk@riflepaperco.com",
              "Subject: SAPB1Int: Error adding Delivery " + o['U_WebOrderId'],
              "",
              "Error adding delivery (ODLN) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually import delivery."
              ])
            username = 'apps@riflepaperco.com'
            password = 'pass'
            server = smtplib.SMTP('smtp.gmail.com:587')
            server.starttls()
            server.login(username,password)
            server.sendmail(fromaddr, toaddrs, msg)
            server.quit()
            raise Exception(error, o['U_WebOrderId'], test, lineNum, o['items'], orderDocEntry, orderDocNum, testarr, delivery.Lines)

        # deliveries =  self.getShipments(num=1, columns=['DocEntry', 'DocTotal', 'DocNum'], params=params)

        find_delivery_sql = """
                            SELECT top 1 dbo.ODLN.DocEntry, dbo.ODLN.DocNum, dbo.ODLN.DocTotal
                            FROM dbo.ODLN
                            WHERE dbo.ODLN.U_WebOrderId='{0}' and dbo.ODLN.DocStatus = 'O'
                            ORDER BY dbo.ODLN.DocNum DESC
                        """.format(o['U_WebOrderId'])

        latestDelivery = self.getLatestOrder(find_delivery_sql)


        deliveryDocEntry = latestDelivery[0]['DocEntry']
        deliveryDocTotal = latestDelivery[0]['DocTotal']
        deliveryDocNum = latestDelivery[0]['DocNum']


        invoice =  com.company.GetBusinessObject(com.constants.oInvoices)

        invoice.DocDate = o['order_date']
        invoice.DocDueDate = o['doc_due_date']
        # invoice.CardCode = 'C105212'
        # #order.NumAtCard = str(o['num_at_card'])
        # # UDF for Magento Web Order ID
        # invoice.UserFields.Fields("U_OrderSource").Value = 'Web Order'
        # invoice.UserFields.Fields("U_WebOrderId").Value = str(o['U_WebOrderId'])
        # invoice.UserFields.Fields("U_TWBS_ShipTo_FName").Value = str(o['shipping_first_name'])
        # invoice.UserFields.Fields("U_TWBS_ShipTo_Lname").Value = str(o['shipping_last_name'])
        # invoice.UserFields.Fields("U_web_order_fname").Value = str(o['order_first_name'])
        # invoice.UserFields.Fields("U_web_order_lname").Value = str(o['order_last_name'])
        # invoice.UserFields.Fields("U_web_orderphone").Value = str(o['order_phone'])
        # invoice.UserFields.Fields("U_web_shipphone").Value = str(o['shipping_phone'])
        # invoice.UserFields.Fields("U_Web_CC_Last4").Value = str(o['cc_last4'])
        # invoice.UserFields.Fields("U_TWBS_ShipTo_Email").Value = str(o['order_email'])

        # if o['cc_type'] == 'MASTERCARD':
        #     invoice.UserFields.Fields("U_web_cc_type").Value = 'MC'
        # elif o['cc_type'] == 'VISA':
        #     invoice.UserFields.Fields("U_web_cc_type").Value = 'VISA'
        # elif o['cc_type'] == 'AMERICAN EXPRESS':
        #     invoice.UserFields.Fields("U_web_cc_type").Value = 'AMEX'
        # elif o['cc_type'] == 'DISCOVER':
        #     invoice.UserFields.Fields("U_web_cc_type").Value = 'DC'

        # if o['user_id']:
        #     invoice.UserFields.Fields("U_WebCustomerID").Value = str(o['user_id'])

        if shiptotal:
            invoice.Expenses.ExpenseCode = 1
            invoice.Expenses.LineTotal = shiptotal[0]['LineTotal']
            invoice.Expenses.TaxCode = 'FLEX'
            invoice.Expenses.BaseDocEntry = deliveryDocEntry
            invoice.Expenses.BaseDocLine = 0
            invoice.Expenses.BaseDocType = 15

        # if 'discount_percent' in o.keys():
        #     invoice.DiscountPercent = o['discount_percent']

        # # Set Shipping Type
        # if 'transport_name' in o.keys():
        #     code = self.getShipCode(o['transport_name'])
        #     invoice.TransportationCode = code


        # # Set Payment Method
        # if 'payment_method' in o.keys():
        #     invoice.PaymentMethod = o['payment_method']

        # ## Set bill to address properties
        # invoice.AddressExtension.BillToCity = o['billto_city']
        # invoice.AddressExtension.BillToCountry = o['billto_country']
        # invoice.AddressExtension.BillToState = o['billto_state']
        # invoice.AddressExtension.BillToStreet = o['billto_address']
        # invoice.AddressExtension.BillToZipCode = o['billto_zipcode']

        # ## Set ship to address properties
        # invoice.AddressExtension.ShipToCity = o['shipto_city']
        # invoice.AddressExtension.ShipToCountry = o['shipto_country']
        # invoice.AddressExtension.ShipToState = o['shipto_state']
        # invoice.AddressExtension.ShipToStreet = o['shipto_address']
        # invoice.AddressExtension.ShipToZipCode = o['shipto_zipcode']

        # # Set Comments
        # if 'comments' in o.keys():
            # invoice.Comments = o['comments']

        i = 0
        for item in o['items']:
            # invoice.Lines.SetCurrentLine(i)
            invoice.Lines.ItemCode = item['itemcode']
            invoice.Lines.Quantity = float(item['quantity'])
            #delivery.Lines.BaseEntry = float(orderDocEntry)
            invoice.Lines.TaxCode = 'FLEX'

            if item.get('price'):
                invoice.Lines.UnitPrice = float(item['price'])

            find_odln_linenum_sql="""SELECT dbo.DLN1.LineNum
                                    FROM dbo.ODLN INNER JOIN dbo.DLN1 ON dbo.ODLN.DocEntry = dbo.DLN1.DocEntry
                                    WHERE dbo.ODLN.DocNum = '{0}' and dbo.DLN1.ItemCode = '{1}'
                                    """.format(deliveryDocNum, item['itemcode'])

            lineNum = self.getLineNum(find_odln_linenum_sql)
            test = int(lineNum[0]['LineNum'])
            invoice.Lines.BaseLine = test
            #delivery.Lines.BaseRef = orderDocNum
            invoice.Lines.BaseType = 15
            invoice.Lines.BaseEntry = int(deliveryDocEntry)
            invoice.Lines.Add()
            i = i + 1
        if o['order_tax'] != '0.00':
            find_odln_linenum_sql="""SELECT dbo.DLN1.LineNum
                                    FROM dbo.ODLN INNER JOIN dbo.DLN1 ON dbo.ODLN.DocEntry = dbo.DLN1.DocEntry
                                    WHERE dbo.ODLN.DocNum = '{0}' and dbo.DLN1.ItemCode = 'SALESTAX' and dbo.DLN1.LineStatus = 'O'
                                    """.format(deliveryDocNum)

            lineNum = self.getLineNum(find_odln_linenum_sql)
            if lineNum:
                # invoice.Lines.SetCurrentLine(i)
                test = int(lineNum[0]['LineNum'])
                invoice.Lines.BaseLine = test
                #delivery.Lines.BaseRef = orderDocNum
                invoice.Lines.BaseType = 15
                invoice.Lines.BaseEntry = int(deliveryDocEntry)
                invoice.Lines.ItemCode = 'SALESTAX'
                invoice.Lines.Quantity = 1
                invoice.Lines.TaxCode = 'FLEX'
                invoice.Lines.UnitPrice = o['order_tax']
                invoice.Lines.Add()

        paramsDownPayment = {'U_WebOrderId': {'value': str(o['U_WebOrderId'])}}
        downPayment = self.getDownPayment(num=1, columns=['DocEntry', 'DocNum'], params=paramsDownPayment)

        invoice.DownPaymentsToDraw.DocEntry = downPayment[0]['DocEntry']
        #invoice.DownPaymentsToDraw.DocNumber = downPayment[0]['DocNum']
        invoice.DownPaymentsToDraw.AmountToDraw = deliveryDocTotal


        lRetCode = invoice.Add()
        if lRetCode != 0:
            error = str(self.com_adaptor.company.GetLastError())
            current_app.logger.error(error)
            #Send email error
            fromaddr = 'sapb1@riflepaperco.com'
            toaddrs  = 'helpdesk@riflepaperco.com'
            msg = "\r\n".join([
              "From: sapb1@riflepaperco.com",
              "To: helpdesk@riflepaperco.com",
              "Subject: SAPB1Int: Error adding A/R Invoice " + o['U_WebOrderId'],
              "",
              "Error adding A/R Invoice (OINV) " + o['U_WebOrderId'] + " with error " + error + ". Fix issue, then manually import invoice."
              ])
            username = 'apps@riflepaperco.com'
            password = 'pass'
            server = smtplib.SMTP('smtp.gmail.com:587')
            server.starttls()
            server.login(username,password)
            server.sendmail(fromaddr, toaddrs, msg)
            server.quit()
            raise Exception(error, o['U_WebOrderId'], test, i, deliveryDocEntry)





    def getItems(self, limit=1, columns=None, whs=None, code=None):
        """Retrieve items(products) from SAP B1.  """
        if columns:
            cols = columns
        else:
            cols = 'ItemCode, ItemName, ItmsGrpCod, CreateDate, UpdateDate'

        if whs:
            sql = """SELECT top {0} {1} FROM dbo.OITM
                     WHERE ItemCode in
                         (SELECT ItemCode FROM dbo.OITW
                          WHERE WhsCode = '{2}')""".format(limit, cols, whs)
        elif code:
            sql = """SELECT {0} FROM dbo.OITM
                     WHERE ItemCode = '{1}'""".format(cols, code)
        else:
            sql = """SELECT top {0} {1} FROM dbo.OITM""".format(limit, cols)
        return list(self.sql_adaptor.fetch_all(sql))

    def getPrices(self, limit=1, columns=None, whs=None, code=None):
        """Retrieve prices(products) from SAP B1.  """
        if columns:
            cols = columns
        else:
            cols = 'ItemCode, Price, Currency, Ovrwritten, Factor'

        listNumber = 2  # Lista de Ventas
        if whs:
            sql = """SELECT top {0} {1} FROM dbo.ITM1
                     WHERE PriceList = {2}
                     AND ItemCode in
                         (SELECT ItemCode FROM dbo.OITW
                          WHERE WhsCode = '{3}')""".format(limit, cols, listNumber, whs)
        elif code:
            sql = """SELECT {0} FROM dbo.ITM1
                     WHERE PriceList = {1}
                     AND ItemCode = '{2}'""".format(cols, listNumber, code)
        else:
            sql = """SELECT top {0} {1} FROM dbo.ITM1
                     WHERE PriceList = {2}""".format(limit, cols, listNumber)

        return list(self.sql_adaptor.fetch_all(sql))

    def getStockNum(self, limit=1, columns=None, whs=None, code=None):
        """Retrieve stock(products) from SAP B1."""
        if columns:
            cols = columns
        else:
            cols = 'ItemCode, WhsCode, OnHand, IsCommited'

        wclause = None
        if whs:
            wclause = """ WhsCode = '{0}' """.format(whs)

        if code:
            sql = """SELECT {0} FROM dbo.OITW
                     WHERE ItemCode = '{1}' {2}""".format(cols, code, (" AND " + wclause) if wclause else '')
        else:
            sql = """SELECT top {0} {1} FROM dbo.OITW {2}""".format(limit, cols, (" WHERE " + wclause) if wclause else '')
        print sql
        return list(self.sql_adaptor.fetch_all(sql))
