import datetime, boto3, os, json, logging
from botocore.exceptions import ClientError
import datetime, sys

# Set the global variables
"""
Can Override the global variables using Lambda Environment Parameters - Which can also be fed through CloudFormation Templates
os.environ['OnlyRunningInstances']
os.environ['RetentionDays']
"""
globalVars  = {}
globalVars['Owner']                 = "Miztiik"
globalVars['Environment']           = "Test"
globalVars['REGION_NAME']           = "eu-central-1"
globalVars['tagName']               = "Serverless-AMI-Baker-Bot"
globalVars['findNeedle']            = "AmiBackUp"
globalVars['ReplicateAMI']          = "No"
globalVars['RetentionTag']          = "DeleteOn"
globalVars['RetentionDays']         = "30"
globalVars['OnlyRunningInstances']  = "No"

#ToDo
# Accept day of week * / 0,1,2,3,4,5,6
globalVars['BackUpScheduledDays']   = "AutoDigiBackupSchedule"  
#//if true then it wont reboot. If not present or set to false then it will reboot.
globalVars['InstanceTagNoReboot']     = "AutoDigiNoReboot"


# Set the log format
logger = logging.getLogger()
for h in logger.handlers:
  logger.removeHandler(h)

h = logging.StreamHandler(sys.stdout)
FORMAT = ' [%(levelname)s]/%(asctime)s/%(name)s - %(message)s'
h.setFormatter(logging.Formatter(FORMAT))
logger.addHandler(h)
logger.setLevel(logging.INFO)

# ec2_client = boto3.client('ec2',region_name=globalVars['REGION_NAME'])
ec2_client = boto3.client('ec2')


"""
If User provides different values, override defaults
"""
def setGlobalVars():
    try:
        if os.environ['ReplicateAMI']:
            globalVars['ReplicateAMI']  = os.environ['ReplicateAMI']
        if os.environ['RetentionDays']:
            globalVars['RetentionDays'] = os.environ['RetentionDays']
        if os.environ['OnlyRunningInstances']:
            globalVars['OnlyRunningInstances']  = os.environ['OnlyRunningInstances']
    except KeyError as e:
        logger.error("User Customization Environment variables are not set")
        logger.error('ERROR: {0}'.format( str(e) ) )

"""
This function creates an AMI of all EC2 instances having a tag "AMIBackUp=Yes"
"""
def amiBakerBot():

    imagesBaked = { 'Images':[], 'FailedAMIs':[] }

    # Filter for instances having the needle tag
    FILTER_1 = {'Name': 'tag:' + globalVars['findNeedle'],  'Values': ['YES', 'Yes', 'yes']}

    # Filter only for running instances
    if globalVars['OnlyRunningInstances'] and globalVars['OnlyRunningInstances'] in ('YES', 'Yes', 'yes'):
        FILTER_2 = {'Name': 'instance-state-name', 'Values': ['running']}
    else:
        FILTER_2 = {'Name': 'instance-state-name', 'Values': ['running','stopped']}

    reservations = ec2_client.describe_instances( Filters=[ FILTER_1, FILTER_2 ]).get( 'Reservations', [] )

    instances = sum(
        [
            [i for i in r['Instances']]
            for r in reservations
        ], [])

    logger.info("Number of instances to create AMI = {0}".format( len(instances)) )
    imagesBaked['TotalImagesBaked'] = len(instances)

    for instance in instances:
        # Check if custom 'RetentionDays' Tag is set in any of the Instances.
        try:
            retention_days = [
                int(t.get('Value')) for t in instance['Tags']
                if t['Key'] == 'RetentionDays'][0]
        except IndexError:
            retention_days = int(globalVars['RetentionDays'])
        except ValueError:
            retention_days = int(globalVars['RetentionDays'])
        except Exception as e:
            retention_days = int(globalVars['RetentionDays'])

        
        # Iterate Tags to collect the instance name tag
        NameTxt = ''
        for tag in instance['Tags']:
            if tag['Key'] == 'Name' :
                NameTxt = 'AMI-for-' + tag['Value'] + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%-H-%M')
            else:
                NameTxt = 'AMI-for-' + str(instance['InstanceId']) + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%-H-%M')

        # Find all the blockdevices attached to the instance
        _BlockDeviceMappings=[]
        for blk in instance['BlockDeviceMappings']:
            _BlockDeviceMappings.append({
                "DeviceName": blk['DeviceName'],
                "NoDevice": ""
                })
        # Try and remove the root device from the block device mappings and only include other volumes
        try:
            _BlockDeviceMappings.remove({
                "DeviceName": instance['RootDeviceName'],
                "NoDevice": ""
                })
        except Exception as e:
            imagesBaked['FailedAMIs'].append( {'InstanceId':instance['InstanceId'],'ERROR':str(e), 'Message':'Unable to remove root device'} )
            pass

        try:
            response = ec2_client.create_image(InstanceId = instance['InstanceId'],
                                               Name = NameTxt,
                                               Description  = 'AMI-for-' + str(instance['InstanceId']) + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%-H-%M'),
                                               BlockDeviceMappings = _BlockDeviceMappings,
                                               NoReboot = True
                                               )

            logger.info("AMI created successfully")
            temp_delete_date = datetime.date.today() + datetime.timedelta(days=retention_days)
            temp_delete_fmt = temp_delete_date.strftime('%Y-%m-%d')
            logger.info("Instance-id="+instance['InstanceId']+" Image-id="+response['ImageId']+" Deletion Date="+temp_delete_fmt)
            
            delete_date = datetime.date.today() + datetime.timedelta(days=retention_days)
            delete_fmt = delete_date.strftime('%Y-%m-%d')

            # Add additional tags
            instance['Tags'].append( { 'Key': globalVars['RetentionTag'], 'Value': delete_fmt } )
            instance['Tags'].append( { 'Key': 'ReplicateAMI', 'Value': globalVars['ReplicateAMI'] } )
            
            # Prepare return message
            imagesBaked['Images'].append({'InstanceId':instance['InstanceId'], 
                                          'DeleteOn': delete_fmt,
                                          'AMI-ID':response['ImageId'],
                                          'Tags':instance['Tags']
                                          }
                                         )
        except Exception as e:
            imagesBaked['FailedAMIs'].append( {'InstanceId':instance['InstanceId'], 'ERROR':str(e), 'Message':'Unable to trigger AMI'} )
            pass
    
    # Tag all AMIs
    for ami in imagesBaked['Images']:
        ec2_client.create_tags(Resources = [ ami['AMI-ID'] ],
                               Tags = ami['Tags']
                               )
    return imagesBaked
       
def lambda_handler(event, context):
    setGlobalVars()
    return amiBakerBot()

if __name__ == '__main__':
    lambda_handler(None, None)