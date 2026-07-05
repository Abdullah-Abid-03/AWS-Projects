import boto3
from datetime import datetime, timezone, timedelta

STOP_AFTER = timedelta(days=2)     # stop instance 2 days after launch 
TERMINATE_AFTER = timedelta(days=5) # terminate 5 days after it was stopped 

# Tag keys used to track state between Lambda runs (Lambda has no memory
# between invocations(as it's stateless), so we persist progress on the instance itself)
STOPPED_TAG_KEY = 'AutoStoppedAt'
SNAPSHOT_TAG_KEY = 'AutoSnapshotId'

# NEW: SNS notification setup

SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:484907511038:EC2-Instance-Notification'  # replace with your ARN
sns = boto3.client('sns')

def notify(subject, message):
    """Send an email notification via SNS. Wrapped in try/except so a
    notification failure never breaks the actual cleanup logic."""
    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],  # SNS subject line has a 100-char limit
            Message=message
        )
    except Exception as e:
        print(f"Failed to send SNS notification: {e}")

def lambda_handler(event, context):
    ec2 = boto3.client('ec2')

    # NEW: Stage 1 + 2 — stop-and-snapshot, then later terminate
    stop_and_snapshot_old_running_instances(ec2)
    terminate_old_stopped_instances(ec2)

    cleanup_orphaned_snapshots(ec2)


# ============================================================
# NEW STAGE 1: find running instances older than STOP_AFTER(i.e. 5 minutes),
# snapshot their volumes, then stop them.

def stop_and_snapshot_old_running_instances(ec2):
    now = datetime.now(timezone.utc)

    instances_response = ec2.describe_instances(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    )

    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            launch_time = instance['LaunchTime']  
            age = now - launch_time

            if age < STOP_AFTER:
                continue  # not old enough yet, skip

            # Skip if we've already processed this instance (avoid double snapshots
            # if the Lambda runs again before the instance actually reaches 'stopped')
            tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}
            if STOPPED_TAG_KEY in tags:
                continue

            # Snapshot every EBS volume attached to this instance
            snapshot_ids = []
            for mapping in instance.get('BlockDeviceMappings', []):
                volume_id = mapping.get('Ebs', {}).get('VolumeId')
                if not volume_id:
                    continue
                snap = ec2.create_snapshot(
                    VolumeId=volume_id,
                    Description=f"Auto snapshot before stopping {instance_id}"
                )
                snapshot_ids.append(snap['SnapshotId'])
                # tag this snapshot so the cleanup function never auto-deletes it
                ec2.create_tags(
                    Resources=[snap['SnapshotId']],
                    Tags=[{'Key': 'AutoBackup', 'Value': 'true'}]
                )
                print(f"Created snapshot {snap['SnapshotId']} for volume {volume_id} "
                      f"(instance {instance_id})")
                notify(
                    "EBS Snapshot Created",
                    f"Snapshot {snap['SnapshotId']} was created for volume {volume_id} "
                    f"on instance {instance_id}."
                )

            # Stop the instance
            ec2.stop_instances(InstanceIds=[instance_id])
            print(f"Stopped instance {instance_id} (age {age}, threshold {STOP_AFTER})")
            notify(
                "EC2 Instance Stopped",
                f"Instance {instance_id} was automatically stopped after running for {age} "
                f"(threshold: {STOP_AFTER}). Snapshots: {', '.join(snapshot_ids) or 'none'}."
            )

            # Tag the instance so we know WHEN it was stopped and what snapshots
            # belong to it — this is how the next Lambda run knows what to do
            ec2.create_tags(
                Resources=[instance_id],
                Tags=[
                    {'Key': STOPPED_TAG_KEY, 'Value': now.isoformat()},
                    {'Key': SNAPSHOT_TAG_KEY, 'Value': ','.join(snapshot_ids)},
                ]
            )


# ============================================================
# UPDATED STAGE 2: terminate old stopped instances, then delete
# their EBS volumes once they're fully detached.

def terminate_old_stopped_instances(ec2):
    now = datetime.now(timezone.utc)

    instances_response = ec2.describe_instances(
        Filters=[
            {'Name': 'instance-state-name', 'Values': ['stopped']},
            {'Name': f'tag-key', 'Values': [STOPPED_TAG_KEY]},
        ]
    )

    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            tags = {t['Key']: t['Value'] for t in instance.get('Tags', [])}

            stopped_at_str = tags.get(STOPPED_TAG_KEY)
            if not stopped_at_str:
                continue

            stopped_at = datetime.fromisoformat(stopped_at_str)
            stopped_duration = now - stopped_at

            if stopped_duration >= TERMINATE_AFTER:
                # NEW: capture volume IDs BEFORE terminating — once the
                # instance is gone, we can't look this up anymore
                volume_ids = [
                    mapping['Ebs']['VolumeId']
                    for mapping in instance.get('BlockDeviceMappings', [])
                    if mapping.get('Ebs', {}).get('VolumeId')
                ]

                ec2.terminate_instances(InstanceIds=[instance_id])
                print(f"Terminated instance {instance_id} "
                      f"(stopped for {stopped_duration}, threshold {TERMINATE_AFTER})")
                notify(
                   "EC2 Instance Terminated",
                    f"Instance {instance_id} was automatically terminated after being stopped "
                    f"for {stopped_duration} (threshold: {TERMINATE_AFTER})."
                )

# ============================================================
# Deletes snapshots that are orphaned (no volume) or whose
# volume isn't attached to any running instance.

def cleanup_orphaned_snapshots(ec2):
    response = ec2.describe_snapshots(OwnerIds=['self'])

    instances_response = ec2.describe_instances(
        Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
    )
    active_instance_ids = set()
    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            active_instance_ids.add(instance['InstanceId'])

    for snapshot in response['Snapshots']:
        snapshot_id = snapshot['SnapshotId']
        # never auto-delete our own intentional backup snapshots
        tags = {t['Key']: t['Value'] for t in snapshot.get('Tags', [])}
        if tags.get('AutoBackup') == 'true':
            continue
        volume_id = snapshot.get('VolumeId')
        if not volume_id:
            ec2.delete_snapshot(SnapshotId=snapshot_id)
            print(f"Deleted EBS snapshot {snapshot_id} as it was not attached to any volume.")
        else:
            try:
                volume_response = ec2.describe_volumes(VolumeIds=[volume_id])
                if not volume_response['Volumes'][0]['Attachments']:
                    ec2.delete_snapshot(SnapshotId=snapshot_id)
                    print(f"Deleted EBS snapshot {snapshot_id} as it was taken from a volume "
                          f"not attached to any running instance.")
            except ec2.exceptions.ClientError as e:
                if e.response['Error']['Code'] == 'InvalidVolume.NotFound':
                    ec2.delete_snapshot(SnapshotId=snapshot_id)
                    print(f"Deleted EBS snapshot {snapshot_id} as its associated volume was not found.")