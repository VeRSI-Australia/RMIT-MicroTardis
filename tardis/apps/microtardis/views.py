import os
import Image
import imghdr
import struct
import csv

from django.template import Context
from django.http import HttpResponse
from django.views.decorators.cache import never_cache
from django.conf import settings
from django.utils import simplejson as json
from django.shortcuts import render_to_response

from tardis.tardis_portal.auth import decorators as authz
from tardis.tardis_portal.shortcuts import render_response_index
from tardis.tardis_portal.staging import add_datafile_to_dataset
from tardis.tardis_portal.staging import write_uploaded_file_to_dataset

from tardis.tardis_portal.models import DatafileParameterSet
from tardis.tardis_portal.models import Schema
from tardis.tardis_portal.models import Dataset
from tardis.tardis_portal.models import Dataset_File

@never_cache
@authz.datafile_access_required
def retrieve_parameters(request, dataset_file_id):
    # get schema id of EDAX Genesis spectrum schema
    schema_spc = Schema.objects.filter(name="EDAXGenesis_SPC")
    schema_ids_spc = []
    for schema in schema_spc:
        schema_ids_spc.append(schema.id)
    field_order_spc = ["Sample Type (Label)", "Preset", "Live Time", "Acc. Voltage"]
    
    # get schema id of EXIF image metadata schema
    schema_exif = Schema.objects.filter(name__endswith="EXIF")
    schema_ids_exif = []
    for schema in schema_exif:
        schema_ids_exif.append(schema.id)
    field_order_exif = ["[User] Date", "[User] Time"]

    datafileparametersets = DatafileParameterSet.objects.filter(dataset_file__pk=dataset_file_id)
    parametersets = {}
    for parameterset in datafileparametersets:
        unsorted = {}
        sorted = []
        # get list of parameters
        parameters = parameterset.datafileparameter_set.all()
        for parameter in parameters:
            unsorted[str(parameter.name.full_name)] = parameter
                
        # sort spectrum tags
        if parameterset.schema.id in schema_ids_spc:
            # sort spectrum tags defined in field_order_spc                
            for field in field_order_spc:
                if field in unsorted:
                    sorted.append(unsorted[field])
                    unsorted.pop(field)
            # sort atomic peak numbers
            peaks = []
            for field in unsorted:
                if field.startswith("Peak ID Element"):
                    peaks.append(field)
            peaks.sort(key=lambda peak: int(peak.split(" ")[-1])) 
            for field in peaks:
                sorted.append(unsorted[field])
                unsorted.pop(field)
            # sort the rest of unsorted parameters
            if unsorted:
                sorted_keys = unsorted.keys()
                sorted_keys.sort()
                for key in sorted_keys:
                    sorted.append(unsorted[key])
            parametersets[parameterset.schema] = sorted
        # sort exif tags
        elif parameterset.schema.id in schema_ids_exif:
            # sort exif metadata tags defined in field_order_exif
            for field in field_order_exif:
                if field in unsorted:
                    sorted.append(unsorted[field])
                    unsorted.pop(field)
            # sort the rest of unsorted parameters
            if unsorted:
                sorted_keys = unsorted.keys()
                sorted_keys.sort()
                for key in sorted_keys:
                    sorted.append(unsorted[key])
            parametersets[parameterset.schema] = sorted
        # use default order
        else:
            parametersets[parameterset.schema] = parameters
    
    thumbpath = None
    qs = Dataset_File.objects.filter(id=dataset_file_id)
    if qs:
        datafile = qs[0]
        basepath = "/thumbnails/small"
        thumbname = str(datafile.id)
        thumbpath = os.path.join(basepath, thumbname)

    c = Context({'parametersets': parametersets,
                 'thumbpath': thumbpath,
                 'datafile_id': dataset_file_id})

    return HttpResponse(render_response_index(request,
                        'tardis_portal/ajax/parameters.html', c))

def write_thumbnails(datafile, img):
    basepath = settings.THUMBNAILS_PATH
    if not os.path.exists(basepath):
        os.makedirs(basepath)
    
    # [ThumbSize, Extenstion]
    thumbnails = [(None,       ".jpg"),
                  ((400, 400), "_small.jpg")
                  ]
    
    for thumb in thumbnails:
        size = thumb[0]
        extention = thumb[1]
        if size: # None for creating thumbnail with original size
            img.thumbnail( size, Image.ANTIALIAS )
        if img.mode != "L": 
            # "L": 8-bit grayscale TIFF images, PIL can process it without problem.
            # "I;16": 16-bit grayscale TIFF images, need conversion before processing it.
            img = img.convert('I')
            table=[ i/256 for i in range(65536) ]
            img = img.point(table, 'L')
        thumbname = str(datafile.id) + extention
        thumbpath = os.path.join(basepath, thumbname)
        out = file(thumbpath, "w")
        try:
            img.save(out, "JPEG")
        finally:
            out.close()
        
def display_thumbnails(request, size, datafile_id):
    basepath = settings.THUMBNAILS_PATH
    datafile = Dataset_File.objects.get(pk=datafile_id)
    extention = ".jpg"
    if size == 'small':
        extention = "_small.jpg"
    thumbname = str(datafile.id) + extention
    thumbpath = os.path.join(basepath, thumbname)
    image_data = open(thumbpath, "rb").read()

    return HttpResponse(image_data, mimetype="image/jpeg")

def direct_to_thumbnail_html(request, datafile_id):
    return render_to_response("thumbnail.html", {"datafile_id": datafile_id})

def get_spectrum_values(datafile):
    basepath = settings.FILE_STORE_PATH
    experiment_id = str(datafile.dataset.experiment.id)
    dataset_id = str(datafile.dataset.id)
    raw_path = datafile.url.partition('//')[2]
    file_path = os.path.join(basepath,
                            experiment_id,
                            dataset_id,
                            raw_path)
    spc = open(file_path)
    offset = 3840
    channel = 4000 # number of spectrum channels
    format = 'i' # long integer
    byte_size = 4
    
    spc.seek(offset)
    values_tuple = struct.unpack(format * channel, spc.read(byte_size * channel))
    
    return values_tuple

def get_spectrum_csv(request, datafile_id):
    datafile = Dataset_File.objects.get(pk=datafile_id)
    filename = str(datafile.url).split('/')[-1][:-4].replace(' ', '_')
    response = HttpResponse(mimetype='text/csv')
    response['Content-Disposition'] = 'attachment; filename=%s.csv' % filename
    writer = csv.writer(response)
    values = get_spectrum_values(datafile)
    index = 0
    for value in values:
        index += 1
        row = [index, value]
        writer.writerow(row)

    return response

def get_spectrum_json(request, datafile_id):
    datafile = Dataset_File.objects.get(pk=datafile_id)
    filename = str(datafile.url).split('/')[-1][:-4].replace(' ', '_')
    values = get_spectrum_values(datafile)
    index = 0
    data = []
    for value in values:
        data.append([index, value])
        index += 1
    content = '{"label": "%s", "data": %s}' % (filename, json.dumps(data))
    response = HttpResponse(content, mimetype='application/json')
    
    return response