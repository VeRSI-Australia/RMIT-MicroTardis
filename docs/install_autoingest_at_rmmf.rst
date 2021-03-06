Setting up automatic ingesting at RMMF
===========================================

 In order for MicroTardis to automatically harvest user data from support PCs at RMMF, four things are required:
 
 1. An Rsync client (DeltaCopy) installed on each of the support PCs. (As of writing - May 2012 -  this is done).
 2. Harvest scripts and cron jobs installed on harvest machine. (As of writing, this is datapuller.isis.rmit.edu.au, and has been done).
 3. Atom dataset provider installed on harvest machine. (As of writing, this is datapuller.isis.rmit.edu.au and has been done).
 4. Atom ingest installed and configured on MicroTardis machine. (As of writing, this has only been done on microtardis-test.eres.rmit.edu.au)
 
1. Installing DeltaCopy
-----------------------
Straightforward. Download, install. It's free. Make it run as a service all the time. Make a note of the IP address and 
name of shared folder.
 
2. Installing harvest scripts
-----------------------------
.. highlight: bash

The scripts are simply bash scripts that use rsync to copy files over from the three support PCs. They should be triggered by a cron job::

    cd /usr/local
    git clone https://github.com/stevage/MicroTardis-Harvest    
     
To configure which individual machines to harvest from and where to store data to, modify the scripts - they are commented.  
 
3. Install atom dataset provider
--------------------------------
The Atom dataset provider is a server which, when requested, scans the staging area directories and provides a list of the most
recent files as an Atom feed, so they can be ingested into Tardis. 

Get the source code here: https://github.com/stevage/atom-dataset-provider

It is installed on the Harvest machine in `/usr/local/microtardis/atom-dataset-provider`.

Use this script to start it::

    #!/bin/bash
    # /usr/local/microtardis/atom-dataset-provider/provider.sh
    bash -x ./kill-provider.sh
    set -x
    LOG=`pwd`/../logs/atom-dataset-provider.log
    PATTERN='([EeSs][0-9]+)'
    EXCLUDEPATTERN='[Tt]humbs.db|e80940/.*/Old\ Data/'
    GROUPPATTERN='/^('"${STAGING}${PATTERN}"'/[^/]+/[^/]+/[^/]+/).+$/'
    PATH=/usr/local/microtardis/local/bin:$PATH
    STAGING=/mnt/np_staging/
    nohup `pwd`/bin/atom-dataset-provider -d "$STAGING"  --group-pattern "$GROUPPATTERN" --exclude-pattern "$EXCLUDEPATTERN" >> $LOG &

The variables `GROUPPATTERN`, `PATTERN` and `EXCLUDEPATTERN` are regular expressions which define which files and folders are included on the 
atom feed, and how folders are grouped as datasets.

By default, it runs on port 4000. On the datapuller machine, Apache must be configured to forward "http://datapuller.isis.rmit.edu.au/atom"
to local port 4000.

4. Install atom ingest 
----------------------
.. note::

Two scripts described here (``auto_ingest.sh`` and ``killcelery.sh``) require sudo privileges. It is probably possible to find an alternative way to install that doesn't require sudo.

.. highlight: bash

Atom ingest is a Django "app" that is installed inside MyTardis. It has no user interface, but runs in the background to periodically
poll the Atom dataset provider for data. When new data is found, it creates records in MyTardis, then copies the files over to the Tardis store::


    cd /opt/mytardis/tardis/apps
    sudo mkdir mytardis-app-atom
    sudo chown <youruser> mytardis-app-atom

Get the source code. Currently this is in my (Steve Bennett's) GitHub repository, but this may change:: 

    git clone https://github.com/stevage/mytardis-app-atom

The code needs to be visible to the Apache user::

    sudo chmod g+r -R mytardis-app-atom

Module names with hyphens cause problems for Python, so we rename it::

    mv mytardis-app-atom atom

.. highlight: python

The Atom ingest app can be configured with many different policies for user creation, experiment creation etc. Edit the
file ``atom/options.py``. These are the settings we currently use::

    class IngestOptions:
    
        # Names of parameters, must match fixture entries.
        # Some are also used for <category> processing in the feed itself.
        PARAM_ENTRY_ID = 'EntryID'
        PARAM_EXPERIMENT_ID = 'ExperimentID'
        PARAM_UPDATED = 'Updated'
        PARAM_EXPERIMENT_TITLE = 'ExperimentTitle'
        
        ALLOW_EXPERIMENT_CREATION = True         # Should we create new experiments
        ALLOW_EXPERIMENT_TITLE_MATCHING = True   # If there's no id, is the title enough to match on
        ALLOW_UNIDENTIFIED_EXPERIMENT = False    # If there's no title/id, should we process it as "uncategorized"?
        DEFAULT_UNIDENTIFIED_EXPERIMENT_TITLE="Uncategorized Data"
        ALLOW_UNNAMED_DATASETS = True            # If a dataset has no title, should we ingest it with a default name
        DEFAULT_UNNAMED_DATASET_TITLE = '(assorted files)'
        ALLOW_USER_CREATION = False              # If experiments belong to unknown users, create them?
        # Can existing datasets be updated? If not, we ignore updates. To cause a new dataset to be created, the incoming
        # feed must have a unique EntryID for the dataset (eg, hash of its contents).
        ALLOW_UPDATING_DATASETS = True
        # If a datafile is modified, do we re-harvest it (creating two copies)? Else, we ignore the update. False is not recommended.
        ALLOW_UPDATING_DATAFILES = True                     
        HIDE_REPLACED_DATAFILES = True 
        # If files are served as /user/instrument/experiment/dataset/datafile.tif
        # then 'datafile.tif' is at depth 5. This is so we can maintain directory structure that
        # is significant within a dataset. Set to -1 to assume the deepest directory.
    
        DATAFILE_DIRECTORY_DEPTH = 7 # /mnt/rmmf_staging/e123/NovaNanoSEM/exp1/ds1/test3.tif
    
        # Yes, we want to extract metadata from ingested files.
        USE_MIDDLEWARE_FILTERS = True
    
        # If we can transfer files "locally" (ie, via SMB mount), then replace URL_BASE_TO_REPLACE with LOCAL_SOURCE_PATH
        # to construct a file path that can be copied from. 
        USE_LOCAL_TRANSFERS = True
        URL_BASE_TO_REPLACE = "http://datapuller.isis.rmit.edu.au/"
        LOCAL_SOURCE_PATH = "/mnt/rmmf_staging/"
    
        # Should we always examine every dataset entry in the feed, even after encountering "old" entries?
        ALWAYS_PROCESS_FULL_FEED = False
    
        HTTP_PROXY = "http://bproxy.rmit.edu.au:8080"

It is likely these will need to be changed as requirements change. In particular, ALLOW_EXPERIMENT_CREATION 
may need to be turned off - it is useful for importing large amounts of data initially.
    
Next, configure the CeleryD tasks that fire the auto ingest. CeleryD is a scheduling mechanism used by MyTardis.

If the file ``atom/settings_atom.py`` doesn't exist, create it. Make its contents as follows::

    # Settings to ensure atom ingest is triggered by celery.
    import djcelery
    from datetime import timedelta

    CELERYBEAT_SCHEDULE = {
      # Every minute, check for new datasets.
      "update-feeds": {
          "task": "atom_ingest.walk_feed",
          "schedule": timedelta(seconds=60),
          "args": ('http://datapuller.isis.rmit.edu.au/atom',)
      },
      # Less frequently, do a full harvest to see if we have missed anything.
      "update-feeds-full": {
          "task": "atom_ingest.walk_feed",
          "schedule": timedelta(seconds=900),
          "args": ('http://datapuller.isis.rmit.edu.au/atom', True)
      },
    }
    
    # Multiple concurrent tasks makes logs complicated and doesn't improve performance.
    CELERYD_CONCURRENCY = 1 
    djcelery.setup_loader()

Now, install the app into MyTardis. In ``/opt/mytardis/tardis/settings.py``, find the line ``"INSTALLED_APPS = ("tardis.microtardis",) + INSTALLED_APPS"``. Add two lines as follows::
  
    INSTALLED_APPS = ("tardis.microtardis",) + INSTALLED_APPS
    INSTALLED_APPS = ("tardis.apps.atom",) + INSTALLED_APPS
    from tardis.apps.atom.settings_atom import *
        
Note the "``tardis.apps.atom``" name matches the directory structure: ``tardis/apps/atom``. 

.. highlight: bash

The app is now installed, but CeleryD is not running. Create this script in /opt/mytardis/tardis/autoingest.sh::

    #!/bin/bash -x
    if [ `whoami` != root ]; then
        echo This script needs to be run as sudo.
        exit
    fi
    LOG=/var/www/html/mytardis/autoingest.log
    sudo -u apache bash -c "nohup `pwd`/bin/django celeryd --beat --purge --loglevel=INFO >> $LOG &"
    
To be able to stop the app, create this script in /opt/mytardis/tardis/killcelery.sh::

    #!/bin/bash -x
    ps ax | grep "[c]eleryd" | awk {'print $1}' | xargs kill -9

And of course::

    chmod a+x autoingest.sh killcelery.sh

To start the autoingest:: 

    ./autoingest.sh ; tail -f autoingest.log    


Troubleshooting
---------------
If you get errors of this type::

    ... 
    File "/opt/mytardis/tardis/apps/atom/atom_ingest.py", line 16, in <module>
      from tardis.tardis_portal.util import get_local_time, get_utc_time, get_local_time_naive
    ImportError: cannot import name get_local_time_naive

You need to install some bug fixes to the MyTardis code::

    $ cd /opt/mytardis/tardis
    $ git remote add steve https://stevage@github.com/stevage/mytardis-ruggedisation.git
    $ git fetch steve 
    
(If a password is requested, press enter.)::    
    
    $ git merge steve/atom-ingest-fixes

 