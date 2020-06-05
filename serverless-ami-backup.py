import datetime, boto3, os, json, logging
from botocore.exceptions import ClientError
import datetime, sys

# Set the global variables
"""
Can Override the global variables using Lambda Environment Parameters - Which can also be fed through CloudFormation Templates
os.environ['OnlyRunningInstances']
os.environ['RetentionDays']
"""
globalVars = {}

# Set the log format
logger = logging.getLogger()
for h in logger.handlers:
    logger.removeHandler(h)

h = logging.StreamHandler(sys.stdout)
FORMAT = ' [%(levelname)s]/%(asctime)s/%(name)s - %(message)s'
h.setFormatter(logging.Formatter(FORMAT))
logger.addHandler(h)
logger.setLevel(logging.INFO)




def boolval(v):
    return v in ("yes", "true", "t", "1", True, 1)

def _dict_to_aws_tags(tags):
    return [{'Key': key, 'Value': value} for (key, value) in tags.items() if not key.startswith('aws:')]

def _aws_tags_to_dict(aws_tags):
    return {x['Key']: x['Value'] for x in aws_tags if not x['Key'].startswith('aws:')}


"""
If User provides different values via event JSON, override defaults
"""
def setGlobalVars(event):

    # ToDo Features
    # Accept day of week * / 0,1,2,3,4,5,6
    globalVars['BackUpScheduledDays'] = "AutoDigiBackupSchedule"
    # if true then it wont reboot. If not present or set to false then it will reboot.
    globalVars['InstanceTagNoReboot'] = "AutoDigiNoReboot"

    try:
        globalVars['Owner'] = event.get('Owner', "Miztiik")
        globalVars['Environment'] = event.get('Environment', "Test")
        globalVars['REGION_NAME'] = event.get('REGION_NAME', "eu-central-1")
        globalVars['tagName'] = event.get('tagName', "Serverless-AMI-Baker-Bot")
        globalVars['findNeedle'] = event.get('findNeedle', "AMIBackUp")
        globalVars['ReplicateAMI'] = event.get('ReplicateAMI', "No")
        globalVars['RetentionTag'] = event.get('RetentionTag', "DeleteOn")
        globalVars['RetentionDays'] = event.get('RetentionDays', "30")
        globalVars['OnlyRunningInstances'] = event.get('OnlyRunningInstances', "No")
        globalVars['SNSTopicArn'] = event.get('SNSTopicArn', "")
    except Exception as e:
        logger.error("ERROR: problem setting globalVars - {0}".format( str(e) ) )


"""
This function creates an AMI of *all* EC2 instances having a tag "AMIBackUp=Yes"
"""
def amiBakerBot():

    # ec2_client = boto3.client('ec2',region_name=globalVars['REGION_NAME'])
    ec2_client = boto3.client('ec2')

    imagesBaked = { 'Images':[], 'FailedAMIs':[], 'Status':{} }

    # Filter for instances having the needle tag
    FILTER_1 = {'Name': 'tag:' + globalVars.get('findNeedle'),  'Values': ['true','YES', 'Yes', 'yes']}

    # Filter only for running instances
    if globalVars.get('OnlyRunningInstances', 'No') in ('true', 'YES', 'Yes', 'yes'):
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
    imagesBaked['Status']['TotalImages'] = len(instances)

    for instance in instances:
        # Check if custom 'RetentionDays' Tag is set in any of the Instances.
        try:
            retention_days = [
                int(t.get('Value')) for t in instance['Tags']
                if t['Key'] == 'RetentionDays'][0]
        except (IndexError, ValueError, Exception) as e:
            retention_days = int(globalVars.get('RetentionDays'))

        # Add additional tags
        newTags = {'Tags':[]}

        # Iterate Tags to collect the instance name tag
        NameTxt = 'AMI-for-' + str(instance['InstanceId']) + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%-H-%M')
        for tag in instance['Tags']:
            if tag['Key'] == 'Name' :
                NameTxt = 'AMI-for-' + tag['Value'] + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%-H-%M')  
                # Set the Name tag
                newTags['Tags'].append( { 'Key': 'Name', 'Value': tag['Value'] } )

        # Find all the blockdevices attached to the instance
        _BlockDeviceMappings = []
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
            imagesBaked['FailedAMIs'].append( {'InstanceId':instance['InstanceId'],
                                                'ERROR':str(e),
                                                'Message':'Unable to remove root device'} )
            continue

        try:
            response = ec2_client.create_image(InstanceId = instance['InstanceId'],
                                                Name = NameTxt,
                                                Description  = 'AMI-for-' + str(instance['InstanceId']) + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%-H-%M'),
                                                # ToDo: Not able to get only the additional disk in device mappings
                                                # BlockDeviceMappings = _BlockDeviceMappings,
                                                NoReboot = True
            #Polls EC2.Client.describe_images() every 15 seconds until a successful state is reached. An error is returned after 40 failed checks.
            logger.info("Waiting for AMI "+response['ImageId']+" to be available")
            waiter = ec2_client.get_waiter('image_available')
            waiter.wait(Filters=[{'Name':'image-id','Values':[response['ImageId']]}])
            logger.info(f'{{ "image_id" : "{ response["ImageId"] }", "completed_backup": True }}')
                                            )
        except Exception as e:
            imagesBaked['FailedAMIs'].append( {'InstanceId':instance['InstanceId'],
                                                'ERROR':str(e),
                                                'Message':'Unable to trigger AMI'} )
            continue

        logger.info("AMI created successfully")
        temp_delete_date = datetime.date.today() + datetime.timedelta(days=retention_days)
        temp_delete_fmt = temp_delete_date.strftime('%Y-%m-%d')
        logger.info("Instance-id="+instance['InstanceId']+" Image-id="+response['ImageId']+" Deletion Date="+temp_delete_fmt)
        
        delete_date = datetime.date.today() + datetime.timedelta(days=retention_days)
        delete_fmt = delete_date.strftime('%Y-%m-%d')

        newTags['Tags'].append( { 'Key': globalVars.get('RetentionTag'), 'Value': delete_fmt } )
        newTags['Tags'].append( { 'Key': 'ReplicateAMI', 'Value': globalVars.get('ReplicateAMI') } )
        newTags['Tags'].append( { 'Key': 'OriginalInstanceID', 'Value': instance['InstanceId'] } )

        logger.info(newTags)
        # Prepare return message
        imagesBaked['Images'].append({'InstanceId':instance['InstanceId'], 
                                        'DeleteOn': delete_fmt,
                                        'AMI-ID':response['ImageId'],
                                        'Tags':newTags['Tags']
                                        }
                                    )

    imagesBaked['Status']['BakedImages'] = len( imagesBaked['Images'] )

    if imagesBaked['Status']['BakedImages'] < imagesBaked['Status']['TotalImages']:
        imagesBaked['Status']['Description'] = 'Partial Success, Check logs'
    elif imagesBaked['Status']['BakedImages'] == imagesBaked['Status']['TotalImages']:
        imagesBaked['Status']['Description'] = 'Success'
    else:
        imagesBaked['Status']['Description'] = 'Failed, More Images'
    # Tag all AMIs
    for ami in imagesBaked.get('Images'):
        ec2_client.create_tags(Resources = [ ami['AMI-ID'] ],
                                Tags = ami['Tags']
                            )

        # Get the Snapshot ID to tag it with metadata
        account_ids = list()
        account_ids.append( boto3.client('sts').get_caller_identity().get('Account') )
        snapResp = ec2_client.describe_images( ImageIds = [ ami['AMI-ID'] ], Owners = account_ids )['Images'][0]
        logger.info('Beginning to tag Snaps')
        for dev in snapResp['BlockDeviceMappings']:
            if 'Ebs' in dev:
                snapTags =  ami['Tags'][:]
                snapTags.append( {'Value': 'Snap-for-' + ami['AMI-ID'], 'Key': 'Name'} )
                ec2_client.create_tags(Resources = [ dev['Ebs']['SnapshotId'] ],
                                        Tags = snapTags
                                    )
    return imagesBaked


def push_to_sns(imagesBaked):
    sns_client = boto3.client('sns')
    try:
        response = sns_client.publish(
        TopicArn = globalVars.get('SNSTopicArn'),
        Message = json.dumps(imagesBaked),
        Subject = imagesBaked['Status']['Description']
        )
        logger.info('SUCCESS: Pushed AMI Baker Results to SNS Topic')
        return "Successfully pushed to Notification to SNS Topic"
    except KeyError as e:
        logger.error('ERROR: Unable to push to SNS Topic: Check [1] SNS Topic ARN is invalid, [2] IAM Role Permissions{0}'.format( str(e) ) )
        logger.error('ERROR: {0}'.format( str(e) ) )


def lambda_handler(event, context):
    
    setGlobalVars(event)

    bakerResults = amiBakerBot()

    if globalVars.get('SNSTopicArn'):
        push_to_sns(bakerResults)

    return bakerResults

if __name__ == '__main__':
    lambda_handler(None, None)
