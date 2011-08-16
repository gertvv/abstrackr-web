import pdb
import os
import shutil
import datetime
import random
import re
import time
from operator import itemgetter
import csv
import random
import string
import smtplib

from pylons import request, response, session, tmpl_context as c, url
from pylons.controllers.util import abort, redirect
import logging
from repoze.what.predicates import not_anonymous, has_permission
from repoze.what.plugins.pylonshq import ActionProtector
from abstrackr.lib.base import BaseController, render
import abstrackr.model as model
from abstrackr.lib import xml_to_sql
from sqlalchemy import or_, and_, desc
from abstrackr.lib.helpers import literal

import pygooglechart
from pygooglechart import PieChart3D, StackedHorizontalBarChart, StackedVerticalBarChart
from pygooglechart import Axis

# this is the path where uploaded databases will be written to
permanent_store = "/uploads/"

log = logging.getLogger(__name__)

### for term highlighting
NEG_C = "#7E2217"
STRONG_NEG_C = "#FF0000"
POS_C = "#4CC417"
STRONG_POS_C = "#347235"
COLOR_D = {1:POS_C, 2:STRONG_POS_C, -1:NEG_C, -2:STRONG_NEG_C}

CONSENSUS_USER = 0

class ReviewController(BaseController):

    @ActionProtector(not_anonymous())
    def create_new_review(self):
        return render("/reviews/new.mako")
    
    @ActionProtector(not_anonymous())
    def create_review_handler(self):
        # first upload the xml file
        xml_file = request.params['db']
        local_file_path = "." + os.path.join(permanent_store, 
                          xml_file.filename.lstrip(os.sep))
        local_file = open(local_file_path, 'w')
        shutil.copyfileobj(xml_file.file, local_file)
        xml_file.file.close()
        local_file.close()
        
        current_user = request.environ.get('repoze.who.identity')['user']
        new_review = model.Review()
        new_review.name = request.params['name']
        
        # we generate a random code for joining this review
        make_code = lambda N: ''.join(random.choice(string.ascii_uppercase + string.digits) for x in range(N))
        review_q = model.meta.Session.query(model.Review)
        existing_codes = [review.code for review in review_q.all()]
        code_length=10
        cur_code = make_code(code_length)
        while cur_code in existing_codes:
            cur_code = make_code(code_length)
        new_review.code = cur_code
        
        new_review.project_lead_id = current_user.id
        new_review.project_description = request.params['description']
        new_review.date_created = datetime.datetime.now()
        new_review.sort_by = request.params['order']
        screening_mode_str = request.params['screen_mode']
        new_review.screening_mode = \
                 {"Single-screen":"single", "Double-screen":"double", "Advanced":"advanced"}[screening_mode_str]
        new_review.initial_round_size = int(request.params['init_size'])
        model.Session.add(new_review)
        model.Session.commit()
        
        # now parse the uploaded file
        num_articles = None
        if xml_file.filename.endswith(".xml"):
            print "parsing uploaded xml..."
            num_articles = xml_to_sql.xml_to_sql(local_file_path, new_review)
        else:
            print "assuming this is a list of pubmed ids"
            num_articles = xml_to_sql.pmid_list_to_sql(local_file_path, new_review)
        print "done."
        

        if new_review.initial_round_size > 0:
            # make sure they don't specify an initial round size comprising more 
            # articles than are in the review.
            new_review.initial_round_size = min(num_articles, new_review.initial_round_size)
            self._create_initial_task_for_review(new_review.review_id, new_review.initial_round_size)
            
        # if we're single or double- screening, we create a 
        # perpetual task here
        if new_review.screening_mode in (u"single", u"double"):
            self._create_perpetual_task_for_review(new_review.review_id)
            
        # join the person administrating the review to the review.
        self._join_review(new_review.review_id)
        
        c.review = new_review
        return render("/reviews/review_created.mako")
        

    @ActionProtector(not_anonymous())
    def invite_reviewers(self, id):
        emails = request.params['emails'].split(",")
        review = self._get_review_from_id(id)
        for email in emails:
            self._invite_person_to_join(email, review)

        
        return self.admin(id, admin_msg="OK -- sent invites to: %s" % request.params['emails'])

    # @TODO this is redundant with code in account.py --
    # re-factor.
    def _invite_person_to_join(self, email, project):
        subject = "Invitation to join review on abstrackr"
        message = """
            Hi there!

            What luck! You've had the good fortune of being invited to join the project: %s 
            on abstrackr. 

            To do so, you're going to need to sign up for an account, if you don't already have one. 
            Then you'll want to log in, and follow this link: %s. 

            Happy screening.
        """ % (project.name, \
               "http://abstrackr.tuftscaes.org/join/%s" % project.code)

        server = smtplib.SMTP("localhost")
        to = email
        sender = "noreply@abstrackr.tuftscaes.org"
        body = string.join((
            "From: %s" % sender,
            "To: %s" % to,
            "Subject: %s" % subject,
            "",
            message
            ), "\r\n")
        
        server.sendmail(sender, [to], body)

    @ActionProtector(not_anonymous())
    def join_a_review(self):
        review_q = model.meta.Session.query(model.Review)
        c.all_reviews = review_q.all()
        return render("/reviews/join_a_review.mako")
        
    @ActionProtector(not_anonymous())
    def join(self, review_code):
        user_id = request.environ.get('repoze.who.identity')['user']
        review_q = model.meta.Session.query(model.Review)
            
        review_to_join = review_q.filter(model.Review.code==review_code).one()
        self._join_review(review_to_join.review_id)
        redirect(url(controller="account", action="welcome"))
        
    
    @ActionProtector(not_anonymous())
    def leave_review(self, id):
        current_user = request.environ.get('repoze.who.identity')['user']
        self._remove_user_from_review(current_user.id, int(id))
        redirect(url(controller="account", action="welcome"))
        
    @ActionProtector(not_anonymous())
    def remove_from_review(self, reviewer_id, review_id):
        self._remove_user_from_review(reviewer_id, review_id)
        redirect(url(controller="review", action="admin", id=review_id))
        
    def _remove_user_from_review(self, reviewer_id, review_id):
        reviewer_review_q = model.meta.Session.query(model.ReviewerProject)
        reviewer_reviews = reviewer_review_q.filter(and_(\
                 model.ReviewerProject.review_id == review_id, 
                 model.ReviewerProject.reviewer_id==reviewer_id)).all()
                 
        for reviewer_review in reviewer_reviews:
            # note that there should only be one entry;
            # this is just in case.   
            model.Session.delete(reviewer_review)
    
        # next, we need to delete all assignments for this person and review
        assignments_q = model.meta.Session.query(model.Assignment)
        assignments = assignments_q.filter(and_(\
                    model.Assignment.review_id == review_id,
                    model.Assignment.reviewer_id == reviewer_id
        )).all()
        
        for assignment in assignments:
            model.Session.delete(assignment)
            model.Session.commit()
        
        
    @ActionProtector(not_anonymous())
    def export_labels(self, id):
        review_q = model.meta.Session.query(model.Review)
        review = review_q.filter(model.Review.review_id == id).one()
        labels = [",".join(["(internal) id", "pubmed id", "refman id", "labeler", "label"])]
        for citation, label in model.meta.Session.query(\
            model.Citation, model.Label).filter(model.Citation.citation_id==model.Label.study_id).\
              filter(model.Label.review_id==id).all():   
                user_name = self._get_username_from_id(label.reviewer_id)
                labels.append(",".join(\
                   [str(x) for x in \
                    [citation.citation_id, citation.pmid_id, citation.refman_id, user_name, label.label]]))
        
        response.headers['Content-type'] = 'text/csv'
        response.headers['Content-disposition'] = 'attachment; filename=labels_%s.csv' % id
        return "\n".join(labels)
        

    @ActionProtector(not_anonymous())
    def delete_review(self, id):
        review_q = model.meta.Session.query(model.Review)
        review = review_q.filter(model.Review.review_id == id).one()

        # make sure we're actually the project lead
        current_user = request.environ.get('repoze.who.identity')['user']
        if not review.project_lead_id == current_user.id:
            return "<font color='red'>tsk, tsk. you're not the project lead, %s.</font>" % current_user.fullname    
    
        ###
        # should probably re-factor into routines...
        ###
        # first delete all associated citations
        citation_q = model.meta.Session.query(model.Citation)
        citations_for_review = citation_q.filter(model.Citation.review_id == review.review_id).all()        
        for citation in citations_for_review:
            model.Session.delete(citation)
        
        # then delete the associations in the table mapping reviewers to 
        # reviews
        reviewer_review_q = model.meta.Session.query(model.ReviewerProject)
        entries_for_review = reviewer_review_q.filter(model.ReviewerProject.review_id == review.review_id).all()
        for reviewer_review in entries_for_review:
            model.Session.delete(reviewer_review)
            
        label_q = model.meta.Session.query(model.Label)
        labels = label_q.filter(model.Label.review_id == review.review_id).all()
        for l in labels:
            model.Session.delete(l)
            model.Session.commit()
            
        label_feature_q = model.meta.Session.query(model.LabeledFeature)
        labeled_features = label_feature_q.filter(model.LabeledFeature.review_id == review.review_id).all()
        for l in labeled_features:
            model.Session.delete(l)
            model.Session.commit()
            
        priority_q = model.meta.Session.query(model.Priority)
        priorities = priority_q.filter(model.Priority.review_id == review.review_id).all()
        for p in priorities:
            model.Session.delete(p)
            model.Session.commit()
            
        # remove all tasks associated with this review
        task_q = model.meta.Session.query(model.Task)
        tasks = task_q.filter(model.Task.review == review.review_id).all()
        for task in tasks:
            # and any FixedTask entries associated with these
            # tasks
            fa_q = model.meta.Session.query(model.FixedTask)
            fas = fa_q.filter(model.FixedTask.task_id == task.id).all()
            for fa in fas:
                model.Session.delete(fa)
                model.Session.commit()
            model.Session.delete(task)
            model.Session.commit()
    
            
        # ... and any assignments
        assignment_q = model.meta.Session.query(model.Assignment)     
        assignments = assignment_q.filter(model.Assignment.review_id == review.review_id)
        for assignment in assignments:
            model.Session.delete(assignment)
            model.Session.commit()
          
        # finally, delete the review
        model.Session.delete(review)
        model.Session.commit()
        
        redirect(url(controller="account", action="my_projects"))
        
    @ActionProtector(not_anonymous())
    def review_conflicts(self, id):
        '''
        the basic idea here is to find all of the conflicting ids, then
        shove these into a FixedTask type task (i.e., a task with an 
        enumerated set of ids to be labeled). This task is then assigned
        to the project lead (assuemd to be the current user).
        '''
        review_id = id
        conflicting_ids = self._get_conflicts(review_id)
        
        task_q = model.meta.Session.query(model.Task)
        conflicts_task_for_this_review = \
            task_q.filter(and_(model.Task.review == review_id,\
                               model.Task.task_type == "conflict")).all()
        
        # we delete any existing conflict Tasks for this review
        if len(conflicts_task_for_this_review) > 0:
            # we assume there is only one such conflicts Task.
            model.Session.delete(conflicts_task_for_this_review[0])
            model.Session.commit()
        
        ### now create an assignment to review these
        conflict_task = model.Task()
        conflict_task.task_type = "conflict"
        conflict_task.review_id = review_id
        conflict_task.num_assigned = len(conflicting_ids)
        model.Session.add(conflict_task)
        model.Session.commit()
        for conflicting_id in conflicting_ids:
            fixed_entry = model.FixedTask()
            fixed_entry.task_id = conflict_task.id
            fixed_entry.citation_id = conflicting_id
            model.Session.add(fixed_entry)
            model.Session.commit()
            
        # finally, add an assignment to (me). note that (me)
        # is the project lead.
        conflict_a = model.Assignment()
        conflict_a.review_id = review_id
        conflict_a.task_id = conflict_task.id
        conflict_a.date_assigned = datetime.datetime.now()
        conflict_a.assignment_type = conflict_task.task_type
        conflict_a.num_assigned = len(conflicting_ids)
        conflict_a.done_so_far = 0
        model.Session.add(conflict_a)
        model.Session.commit()
        
        redirect(url(controller="review", action="screen", review_id=review_id, assignment_id=conflict_a.id)) 
    
    def _get_labels_for_citation(self, citation_id):
        return model.meta.Session.query(model.Label).\
            filter(model.Label.study_id==citation_id).all()
        
    def _get_conflicts(self, review_id):
        citation_ids_to_labels = {}
        for citation, label in model.meta.Session.query(\
          model.Citation, model.Label).filter(model.Citation.citation_id==model.Label.study_id).\
          filter(model.Label.review_id==review_id).all():
            if citation.citation_id in citation_ids_to_labels.keys():
                citation_ids_to_labels[citation.citation_id].append(label)
            else:
                citation_ids_to_labels[citation.citation_id] = [label]
        
        citation_ids_to_conflicting_labels = {}
        # walk over all of the 
        
        for citation_id in [c_id for c_id in citation_ids_to_labels.keys() if citation_ids_to_labels[c_id]>1]:
            if len(set([label.label for label in citation_ids_to_labels[citation_id]])) > 1 and\
                    not CONSENSUS_USER in [label.reviewer_id for label in citation_ids_to_labels[citation_id]]:
                citation_ids_to_conflicting_labels[citation_id] = citation_ids_to_labels[citation_id]
        
        return citation_ids_to_conflicting_labels
                
              
    @ActionProtector(not_anonymous())
    def admin(self, id, admin_msg=""):
        # make sure we're actually the project lead
        if not self._current_user_leads_review(id):
            return "<font color='red'>tsk, tsk. you're not the project lead, %s.</font>" % current_user.fullname        
            
            
        current_user = request.environ.get('repoze.who.identity')['user']
        c.participating_reviewers = self._get_participants_for_review(id)
        # eliminate our the lead themselves from this list
        c.participating_reviewers = [reviewer for reviewer in c.participating_reviewers if \
                                        reviewer.id != current_user.id]
        
        # for the client side
        c.reviewer_ids_to_names_d = self._reviewer_ids_to_names(c.participating_reviewers)
        c.admin_msg = admin_msg
        return render("/reviews/admin.mako")
            
    @ActionProtector(not_anonymous())
    def assignments(self, id):
        # make sure we're actually the project lead (by the way, should probably
        # handle this verification in a decorator...)
        if not self._current_user_leads_review(id):
            return "<font color='red'>tsk, tsk. you're not the project lead, %s.</font>" % current_user.fullname        
            
        c.participating_reviewers = self._get_participants_for_review(id)
        c.reviewer_ids_to_names_d = self._reviewer_ids_to_names(c.participating_reviewers)
        
        assignments_q = model.meta.Session.query(model.Assignment)
        assignments = assignments_q.filter(model.Assignment.review_id == id)
        c.assignments = assignments
        return render("/reviews/assignments.mako")
    
    def _current_user_leads_review(self, review_id):
        current_user = request.environ.get('repoze.who.identity')['user']
        c.review = self._get_review_from_id(review_id)
        return c.review.project_lead_id == current_user.id

    def _reviewer_ids_to_names(self, reviewers):
        # for the client side
        reviewer_ids_to_names_d = {}
        for reviewer in reviewers:
            reviewer_ids_to_names_d[reviewer.id] = reviewer.username
        reviewer_ids_to_names_d = reviewer_ids_to_names_d
        return reviewer_ids_to_names_d
        
    @ActionProtector(not_anonymous())    
    def participants(self, id):
        # make sure we're actually the project lead (by the way, should probably
        # handle this verification in a decorator...)
        current_user = request.environ.get('repoze.who.identity')['user']
        c.review = self._get_review_from_id(id)
        if not c.review.project_lead_id == current_user.id:
            return "<font color='red'>tsk, tsk. you're not the project lead, %s.</font>" % current_user.fullname        
            
        c.participating_reviewers = self._get_participants_for_review(id)
    
        return render("/reviews/participants.mako")
        
    @ActionProtector(not_anonymous())
    def show_review(self, id):
        review_q = model.meta.Session.query(model.Review)

        c.review = review_q.filter(model.Review.review_id == id).one()
        # grab all of the citations associated with this review
        citation_q = model.meta.Session.query(model.Citation)
        citations_for_review = citation_q.filter(model.Citation.review_id == id).all()
   
        c.num_citations = len(citations_for_review)
        # and the labels provided thus far
        label_q = model.meta.Session.query(model.Label)
        ### TODO first of all, will want to differentiate between
        # unique and total (i.e., double screened citations). will
        # also likely want to pull additional information here, e.g.,
        # the participating reviewers, etc.
        labels_for_review = label_q.filter(model.Label.review_id == id).all()
        c.num_unique_labels = len(set([lbl.study_id for lbl in labels_for_review]))
        c.num_labels = len(labels_for_review)
        
        # generate a pretty plot via google charts
        chart = PieChart3D(500, 200)
        chart.add_data([c.num_citations-c.num_unique_labels, c.num_unique_labels])
        chart.set_colours(['224499', '80C65A'])
        chart.set_pie_labels(['unscreened', 'screened'])
        c.pi_url = chart.get_url()
        
        reviewer_proj_q = model.meta.Session.query(model.ReviewerProject)
        reviewer_ids = [rp.reviewer_id for rp in reviewer_proj_q.filter(model.Citation.review_id == id).all()]

        c.participating_reviewers = reviewers = self._get_participants_for_review(id)
        user_q = model.meta.Session.query(model.User)
        c.project_lead = user_q.filter(model.User.id == c.review.project_lead_id).one()
        
        current_user = request.environ.get('repoze.who.identity')['user']
        c.is_admin = c.project_lead.id == current_user.id
        n_lbl_d = {} # map users to the number of labels they've provided
        for reviewer in reviewers:
            # @TODO problematic if two reviewers have the same fullname, which
            # isn't explicitly prohibited
            n_lbl_d[reviewer.fullname] = len([l for l in labels_for_review if l.reviewer_id == reviewer.id])
        
        # now make a horizontal bar graph showing the amount of work done by reviewers
        workloads = n_lbl_d.items() # first sort by the number of citations screened, descending
        workloads.sort(key = itemgetter(1), reverse=True)
        num_screened = [x[1] for x in workloads]
        names = [x[0] for x in workloads]
        
        
        ### 
        # so, due to what is apparently a bug in the pygooglechart api, 
        # we construct a google charts string explicitly for the horizontal bar graph here.
        height = 30*len(names)+50
        width = 500
        google_url = "http://chart.apis.google.com/chart?cht=bhg&chs=%sx%s" % (width, height)
        chart = StackedHorizontalBarChart(500, 30*len(names)+50, x_range=(0, c.num_labels))
        data_str = "chd=t:%s" % ",".join([str(n) for n in num_screened])
        google_url = "&".join([google_url, data_str])
        max_num_screened = max(num_screened)
        google_url = "&".join([google_url, "chds=0,%s" % max_num_screened])
        # we have to reverse the names here; this seems to be a quirk with
        # google maps. see: http://psychopyko.com/tutorial/how-to-use-google-charts/
        names.reverse()
        google_url = "&".join([google_url, "chxt=y,x&chxl=0:|%s|" % "|".join([name.replace(" ", "%20") for name in names])])
        # now the x axis labels
        x_ticks = [0, int(max_num_screened/3.0), int(max_num_screened/2.0), int(3 * (max_num_screened/4.0)), max_num_screened]
        google_url = "".join([google_url, "1:|%s" % "|".join([str(x) for x in x_ticks])])
        bar_width = 25
        google_url = google_url + "&chbh=%s&chco=4D89F9" % bar_width
        c.workload_graph_url = google_url

        return render("/reviews/show_review.mako")

    @ActionProtector(not_anonymous())
    def relabel_term(self, term_id, new_label):
        term_q = model.meta.Session.query(model.LabeledFeature)
        labeled_term =  term_q.filter(model.LabeledFeature.id == term_id).one()
        labeled_term.label = new_label
        model.Session.add(labeled_term)
        model.Session.commit()
        redirect(url(controller="review", action="review_terms", id=labeled_term.review_id)) 
        
    @ActionProtector(not_anonymous())
    def delete_term(self, id):
        term_id = id
        term_q = model.meta.Session.query(model.LabeledFeature)
        labeled_term = term_q.filter(model.LabeledFeature.id == term_id).one()
        model.Session.delete(labeled_term)
        model.Session.commit()
        redirect(url(controller="review", action="review_terms", id=labeled_term.review_id)) 
        
        
    @ActionProtector(not_anonymous())
    def label_term(self, review_id, label):
        current_user = request.environ.get('repoze.who.identity')['user']
        new_labeled_feature = model.LabeledFeature()
        new_labeled_feature.term = request.params['term']
        new_labeled_feature.review_id = review_id
        new_labeled_feature.reviewer_id = current_user.id
        new_labeled_feature.label = label
        new_labeled_feature.date_created = datetime.datetime.now()
        model.Session.add(new_labeled_feature)
        model.Session.commit()
        
    
    @ActionProtector(not_anonymous())
    def tag_citation(self, review_id, study_id):
        current_user = request.environ.get('repoze.who.identity')['user']
        tags = []
        for key,val in request.params.items():
            if key == "tags[]" and not val in ("", " ", "(enter new tag)"):
                tags.append(val)

       
        # check if any are to be added (i.e., for a new tag)
        existing_tag_types = self._get_tag_types_for_review(review_id)
        
        tags = list(set(tags))
        for user_tag in tags:
            if user_tag not in existing_tag_types:
                self.add_tag(review_id, user_tag)

        # ok -- now, get tags for this citation; we're going to 
        # untag everything first
        cur_tags = self._get_tags_for_citation(study_id, texts_only=False)
        for tag in cur_tags:
            model.Session.delete(tag)
            model.Session.commit()

        # now add all the new tags
        for tag in list(set(tags)):
            new_tag = model.Tags()
            new_tag.creator_id = current_user.id
            new_tag.tag_id = self._get_tag_id(review_id, tag)
            new_tag.citation_id = study_id
            model.Session.add(new_tag)
            model.Session.commit()



    @ActionProtector(not_anonymous())
    def add_tag(self, review_id, tag):

        current_user = request.environ.get('repoze.who.identity')['user']

        # make sure there isn't already an identical tag
        cur_tags = self._get_tag_types_for_review(review_id)
        tag = tag.strip()
        if tag not in cur_tags:
            new_tag = model.TagTypes()
            new_tag.text = tag
            new_tag.review_id = review_id
            new_tag.creator_id = current_user.id
            model.Session.add(new_tag)
            model.Session.commit()


    @ActionProtector(not_anonymous())
    def label_citation(self, review_id, assignment_id, study_id, seconds, label):
        # unnervingly, this commit() must remain here, or sql
        # alchemy seems to get into a funk. I realize this is 
        # cargo-cult programming... @TODO further investigate
        model.meta.Session.commit()
        
        current_user = request.environ.get('repoze.who.identity')['user']
        # check if we've already labeled this; if so, handle
        # appropriately
        label_q = model.meta.Session.query(model.Label)
        existing_label = label_q.filter(and_(
                        model.Label.review_id == review_id, 
                        model.Label.study_id == study_id, 
                        model.Label.reviewer_id == current_user.id)).all()
        # pull the associated assignment object
        
        assignment = self._get_assignment_from_id(assignment_id)
        if len(existing_label) > 0 and not assignment.assignment_type == "conflict":
            # then this person has already labeled this example
            print "(RE-)labeling citation %s with label %s" % (study_id, label)
            existing_label = existing_label[0]
            existing_label.label = label
            existing_label.label_last_updated = datetime.datetime.now()
            existing_label.labeling_time += int(seconds)
            model.Session.add(existing_label)
            model.Session.commit()
            
            # if we are re-labelng, return the same abstract, reflecting the new
            # label.
            c.cur_lbl = existing_label
            c.assignment_id = c.cur_lbl.assignment_id
            citation_q = model.meta.Session.query(model.Citation)
            c.assignment_type = assignment.assignment_type
            c.cur_citation = citation_q.filter(model.Citation.citation_id == study_id).one()
            c.cur_citation = self._mark_up_citation(review_id, c.cur_citation)
            c.review_id = review_id
            return render("/citation_fragment.mako")
        else:
            print "labeling citation %s with label %s" % (study_id, label)
            # first push the label to the database
            new_label = model.Label()
            new_label.label = label
            new_label.review_id = review_id
            new_label.study_id = study_id
            new_label.assignment_id = assignment_id
            new_label.labeling_time = int(seconds)
            
            if assignment.assignment_type == "conflict":
                # the 0th reviewer can be thought of as God
                # i.e., omniscient -- this is taken to be the
                # group consensus user
                new_label.reviewer_id = CONSENSUS_USER
            else:
                new_label.reviewer_id = current_user.id
            new_label.first_labeled = new_label.label_last_updated = datetime.datetime.now()
            model.Session.add(new_label)
            model.Session.commit()
            assignment.done_so_far += 1
            model.Session.commit()
            
            ###
            # for finite assignments, we need to check if we're through.
            if assignment.assignment_type != "perpetual":
                if assignment.done_so_far >= assignment.num_assigned:
                    assignment.done = True
                    model.Session.commit()
            else:
                # for `perpetual' (single, double or n-screening) case, we need to
                # keep track of the priority table. 
                #
                # update the number of times this citation has been labeled; 
                # if we have collected a sufficient number of labels, pop it from
                # the queue
                priority_obj = self._get_priority_for_citation_review(study_id, review_id)
                priority_obj.num_times_labeled += 1
                priority_obj.is_out = False
                model.Session.commit()
                
                # are we through with this citation/review?
                review = self._get_review_from_id(review_id)
        
                if review.screening_mode in ("single", "double"):
                    if priority_obj.num_times_labeled >= 2:
                        model.Session.delete(priority_obj)
                        model.Session.commit()
        
                    # has this person already labeled everything in this review?
                    num_citations_in_review = len(self._get_citations_for_review(review_id))
                    num_screened = len(self._get_already_labeled_ids(review.review_id))
                    if num_screened >= num_citations_in_review:
                        assignment.done = True
                        model.Session.commit()
                    
                    
            return self.screen_next(review_id, assignment_id)
        
    @ActionProtector(not_anonymous())
    def markup_citation(self, id, assignment_id, citation_id):
        citation_q = model.meta.Session.query(model.Citation)
        c.cur_citation = citation_q.filter(model.Citation.citation_id == citation_id).one()
        c.review_id = id
        c.assignment_id = assignment_id
        c.cur_citation = self._mark_up_citation(id, c.cur_citation)
        
        current_user = request.environ.get('repoze.who.identity')['user']
        label_q = model.meta.Session.query(model.Label)
        c.cur_lbl = label_q.filter(and_(
                                     model.Label.study_id == citation_id,
                                     model.Label.reviewer_id == current_user.id)).all()
        if len(c.cur_lbl) > 0:
            c.cur_lbl = c.cur_lbl[0]
        else:
            c.cur_lbl = None
        return render("/citation_fragment.mako")
      
    @ActionProtector(not_anonymous())
    def screen(self, review_id, assignment_id):
        assignment = self._get_assignment_from_id(assignment_id)
        if assignment is None:
            redirect(url(controller="review", action="screen", \
                        review_id=review_id, assignment_id = assignment_id))    
            
        review = self._get_review_from_id(review_id)
        if assignment.done:
            redirect(url(controller="account", action="welcome"))    
           
        c.review_id = review_id
        c.review_name = review.name
        c.assignment_id = assignment_id
        c.assignment_type = assignment.assignment_type
        
        c.cur_citation = self._get_next_citation(assignment, review)
        if c.cur_citation is None:
            if assignment.assignment_type == "conflict":
                return "no conflicts!"
            else:
                return render("/assignment_complete.mako")
        c.cur_citation = self._mark_up_citation(review_id, c.cur_citation)
        
        c.cur_lbl = None
        if assignment.assignment_type == "conflict":
            c.cur_lbl = self._get_labels_for_citation(c.cur_citation.citation_id)
            c.reviewer_ids_to_names_d =  self._labels_to_reviewer_name_d(c.cur_lbl)
        
        # now get tags for this citation
        c.tags = self._get_tags_for_citation(c.cur_citation.citation_id)
        
        # and these are all available tags
        c.tag_types = self._get_tag_types_for_review(review_id)
     
        model.meta.Session.commit()
        return render("/screen.mako")
          

    @ActionProtector(not_anonymous())
    def update_tags(self, study_id):
        # now get tags for this citation
        c.tags = self._get_tags_for_citation(study_id)
        return render("/tag_fragment.mako")

    @ActionProtector(not_anonymous())
    def update_tag_types(self, review_id, study_id):
        # now get tags for this citation
        c.tag_types = self._get_tag_types_for_review(review_id)
        c.tags = self._get_tags_for_citation(study_id)
        return render("/tag_dialog_fragment.mako")

    @ActionProtector(not_anonymous())
    def screen_next(self, review_id, assignment_id):
        assignment = self._get_assignment_from_id(assignment_id)
        review = self._get_review_from_id(review_id)

        c.review_id = review_id
        c.review_name = self._get_review_from_id(review_id).name
        c.assignment_id = assignment_id
        c.assignment_type = assignment.assignment_type

        c.cur_citation = self._get_next_citation(assignment, review)
        
        # but wait -- are we finished?
        if assignment.done or c.cur_citation is None:
            return render("/assignment_complete.mako")
            
        # mark up the labeled terms 
        c.cur_citation = self._mark_up_citation(review_id, c.cur_citation)

        c.cur_lbl = None
        if assignment.assignment_type == "conflict":
            c.cur_lbl = self._get_labels_for_citation(c.cur_citation.citation_id)
            c.reviewer_ids_to_names_d =  self._labels_to_reviewer_name_d(c.cur_lbl)
         
        # now get tags for this citation
        c.tags = self._get_tags_for_citation(c.cur_citation.citation_id)
        
        # and these are all available tags
        c.tag_types = self._get_tag_types_for_review(review_id)
           
        model.meta.Session.commit()
        return render("/citation_fragment.mako")
     
        
    def _labels_to_reviewer_name_d(self, labels):
        reviewer_ids_to_names_d = {}
        for label in labels:
            reviewer_ids_to_names_d[label.reviewer_id] = \
                self._get_username_from_id(label.reviewer_id)
        return reviewer_ids_to_names_d 
        
    def _get_next_citation(self, assignment, review):
        next_id = None
        # if the current assignment is a 'fixed' assignment (i.e.,
        # comprises a finite set of ids to be screened -- an initial round,
        # or conflicting round, e.g.) then we pull from the FixedAssignments table
        if assignment.assignment_type in ("initial", "conflict"):
            # in the case of initial assignments, we never remove the citations,
            # thus we need to ascertain that we haven't already screened it
            #eligible_pool = self._get_init_ids_for_review(review.review_id)
            eligible_pool = self._get_ids_for_task(assignment.task_id)
            # a bit worried about runtime here (O(|eligible_pool| x |already_labeled|)).
            # hopefully eligible_pool shrinks as sufficient labels are acquired (and it 
            # shoudl always be pretty small for initial assignments).
            reviewer_id = None
            if assignment.assignment_type == "conflict":
                # the 0th user is the omniscient consensus reviewer,
                # by convention. in the case that we're reviewing conflicts
                # we use this special user.
                reviewer_id = 0
            already_labeled = self._get_already_labeled_ids(review.review_id, reviewer_id=reviewer_id)
            eligible_pool = [xid for xid in eligible_pool if not xid in already_labeled]
            next_id = None
     
            if len(eligible_pool) > 0:
                next_id = eligible_pool[0]
        else:
            priority = self._get_next_priority(review.review_id)
            if priority is None:
                next_id = None
            else:
                next_id = priority.citation_id
                ## 'check out' / lock the citation
                priority.is_out = True
                priority.locked_by = request.environ.get('repoze.who.identity')['user'].id
                priority.time_requested = datetime.datetime.now()
                model.Session.commit()

        return None if next_id is None else self._get_citation_from_id(next_id)
        
    @ActionProtector(not_anonymous())
    def review_terms(self, id, assignment_id=None):
        review_id = id
        current_user = request.environ.get('repoze.who.identity')['user']
        
        term_q = model.meta.Session.query(model.LabeledFeature)
        labeled_terms =  term_q.filter(and_(\
                                model.LabeledFeature.review_id == review_id,\
                                model.LabeledFeature.reviewer_id == current_user.id
                         )).all()
        c.terms = labeled_terms
        c.review_id = review_id
        c.review_name = self._get_review_from_id(review_id).name
    
        # if an assignment id is given, it allows us to provide a 'get back to work'
        # link.
        c.assignment = assignment_id
        if assignment_id is not None:
            c.assignment = self._get_assignment_from_id(assignment_id)
            
        return render("/reviews/edit_terms.mako")
                         
    @ActionProtector(not_anonymous())
    def review_labels(self, review_id, assignment_id=None):
        current_user = request.environ.get('repoze.who.identity')['user']
        
        label_q = model.meta.Session.query(model.Label)
        already_labeled_by_me = [label for label in label_q.filter(\
                                   and_(model.Label.review_id == review_id,\
                                        model.Label.reviewer_id == current_user.id)).\
                                            order_by(desc(model.Label.label_last_updated)).all()] 
        
        c.given_labels = already_labeled_by_me
        c.review_id = review_id 
        c.review_name = self._get_review_from_id(review_id).name
        
        # now get the citation objects associated with the given labels
        c.citations_d = {}
        for label in c.given_labels:
            c.citations_d[label.study_id] = self._get_citation_from_id(label.study_id)

        # if an assignment id is given, it allows us to provide a 'get back to work'
        # link.
        c.assignment = assignment_id
        if assignment_id is not None:
            c.assignment = self._get_assignment_from_id(assignment_id)
     
        return render("/reviews/review_labels.mako")
            
    @ActionProtector(not_anonymous())
    def show_labeled_citation(self, review_id, citation_id):
        current_user = request.environ.get('repoze.who.identity')['user']
        c.review_id = review_id
        c.review_name = self._get_review_from_id(review_id).name
 
        citation_q = model.meta.Session.query(model.Citation)
        c.cur_citation = citation_q.filter(model.Citation.citation_id == citation_id).one()
        # mark up the labeled terms 
        c.cur_citation = self._mark_up_citation(review_id, c.cur_citation)
        c.assignment_type = None # just leaving this empty
        
        label_q = model.meta.Session.query(model.Label)
        c.cur_lbl = label_q.filter(and_(
                                     model.Label.study_id == citation_id,
                                     model.Label.reviewer_id == current_user.id)).one()
        c.assignment_id = c.cur_lbl.assignment_id

        # finally, grab tags
        c.tag_types = self._get_tag_types_for_review(review_id)
        c.tags = self._get_tags_for_citation(citation_id)

        return render("/screen.mako")
        
    @ActionProtector(not_anonymous())
    def create_assignment(self, id):
        assign_to = request.params.getall("assign_to")
        due_date = None
        try:
            m,d,y = [int(x) for x in request.params['due_date'].split("/")]
            due_date = datetime.date(y,m,d)
        except:
            pass
        p_rescreen = float(request.params['p_rescreen'])
        n = int(request.params['n'])
        assign_to_ids = [self._get_id_from_username(username) for username in assign_to]
        for reviewer_id in assign_to_ids:     
            new_assignment = model.Assignment()
            new_assignment.review_id = id
            new_assignment.reviewer_id = reviewer_id
            new_assignment.date_due = due_date
            new_assignment.done = False
            new_assignment.done_so_far = 0
            new_assignment.num_assigned = n
            new_assignment.p_rescreen = p_rescreen
            new_assignment.date_assigned = datetime.datetime.now()
            model.Session.add(new_assignment)
            model.Session.commit()
        
        redirect(url(controller="review", action="admin", id=id))     
              
        
    def _get_priority_for_citation_review(self, citation_id, review_id):
        priority_q = model.meta.Session.query(model.Priority)
        p_for_cit_review =  priority_q.filter(and_(\
                                model.Priority.review_id == review_id,\
                                model.Priority.citation_id == citation_id,\
                         )).one()
        return p_for_cit_review
        
    def _join_review(self, review_id):
        current_user = request.environ.get('repoze.who.identity')['user']
        
        ###
        # this is super-hacky, but there was a bug that was causing
        # the current_user object to be None for reasons I cannot
        # ascertain. refreshing the page inexplicably works; hence we
        # do it here. need to test this further.
        ####
        if current_user is None:
            return self._join_review(review_id)

        # first, make sure this person isn't already in this review.
        reviewer_review_q = model.meta.Session.query(model.ReviewerProject)
        reviewer_reviews = reviewer_review_q.filter(and_(\
                 model.ReviewerProject.review_id == review_id, 
                 model.ReviewerProject.reviewer_id == current_user.id)).all()
        
        if len(reviewer_reviews) == 0:
            # we only add them if they aren't already a part of the review.
            reviewer_project = model.ReviewerProject()
            reviewer_project.reviewer_id = current_user.id
            reviewer_project.review_id = review_id
            model.Session.add(reviewer_project)
        
            # now we check what type
            review = self._get_review_from_id(review_id)
            if review.screening_mode in (u"single", u"double"):
                # then we automatically add a `perpetual' assignment
                self._assign_perpetual_task(current_user.id, review.review_id)
             
            # assign any initial tasks for this review to the joinee.  
            self._assign_initial_tasks(current_user.id, review.review_id)
            return True
        return False     
        
    def _get_next_priority(self, review_id):
        '''
        returns citation ids to be screened for the specified
        review, ordered by their priority (int he priority table).
        this is effectively how AL is implemented in our case --
        we assume the table has been sorted/ordered by some
        other process.
        
        Note: this will not return ids for instances that are 
        currently being labeled.
        '''
        priority_q = model.meta.Session.query(model.Priority)
        me = request.environ.get('repoze.who.identity')['user'].id
        ranked_priorities = [priority for priority in priority_q.filter(\
                                model.Priority.review_id == review_id).\
                                    order_by(model.Priority.priority).all() if\
                                    not (priority.is_out and priority.locked_by != me)]
                                    
        best_that_i_havent_labeled = None
        already_labeled = self._get_already_labeled_ids(review_id)
        for priority_obj in ranked_priorities:
             if priority_obj.citation_id not in already_labeled:
                 return priority_obj
        # this person has already labeled everything -- nothing more to do!
        return None
        
    def _get_init_ids_for_review(self, review_id):
        init_q = model.meta.Session.query(model.FixedAssignment)
        init_ids = [ia.citation_id for ia in \
                     init_q.filter(and_(\
                            model.FixedAssignment.review_id == review_id,\
                            model.FixedAssignment.assignment_type == "initial")).\
                        order_by(model.FixedAssignment.citation_id).all()]    
        return init_ids
    
    def _get_tag_id(self, review_id, text):
        tag_q = model.meta.Session.query(model.TagTypes)
        
        tag_type = tag_q.filter(and_(
                model.TagTypes.review_id == review_id,
                model.TagTypes.text == text)).one()
        return tag_type.id

    def _get_tags_for_citation(self, citation_id, texts_only=True):
        tag_q = model.meta.Session.query(model.Tags)
        tags = tag_q.filter(model.Tags.citation_id == citation_id).all()
        if texts_only:
            return self._tag_ids_to_texts([tag.tag_id for tag in tags])
        return tags
    
    def _tag_ids_to_texts(self, tag_ids):
        return [self._text_for_tag(tag_id) for tag_id in tag_ids]
    
    def _text_for_tag(self, tag_id):
        tag_type_q = model.meta.Session.query(model.TagTypes)
        tag_obj = tag_type_q.filter(model.TagTypes.id == tag_id).one()
        return tag_obj.text

    def _get_tag_types_for_citation(self, citation_id, objects=False):
        tags = self._get_tags_for_citation(citation_id)
        # now map those types to names
        tag_type_q = model.meta.Session.query(model.TagTypes)
        tags = []
        
        for tag in tags:
            tag_obj = tag_type_q.filter(model.TagTypes.id == tag.tag_id).one()
     
            if objects:
                tags.append(tag_obj)
            else:
                tags.append(tag_obj.text)
        
        return tags

    def _get_tag_types_for_review(self, review_id):
        tag_q = model.meta.Session.query(model.TagTypes)
        tag_types = tag_q.filter(model.TagTypes.review_id == review_id).all()
        return [tag_type.text for tag_type in tag_types]


    def _get_ids_for_task(self, task_id):
        q = model.meta.Session.query(model.FixedTask)
        eligible_ids = [fixed_task.citation_id for fixed_task in \
                                q.filter(model.FixedTask.task_id == task_id).\
                                order_by(model.FixedTask.citation_id).all()]    
        return eligible_ids
        
    def _get_already_labeled_ids(self, review_id, reviewer_id=None):
        ''' 
        returns a list of citation ids corresponding to those citations that
        the current reviewer (or the reviewer specified by user_id) has labeled 
        for the specified review.
        '''
        if reviewer_id is None:
            reviewer_id = request.environ.get('repoze.who.identity')['user'].id
        
        label_q = model.meta.Session.query(model.Label)
        already_labeled_ids = [label.study_id for label in label_q.filter(and_(\
                                                    model.Label.review_id == review_id,\
                                                    model.Label.reviewer_id == reviewer_id)).all()]
        return already_labeled_ids 
        
    def _get_participants_for_review(self, review_id):
        reviewer_proj_q = model.meta.Session.query(model.ReviewerProject)
        reviewer_ids = \
            list(set([rp.reviewer_id for rp in reviewer_proj_q.filter(model.ReviewerProject.review_id == review_id).all()]))
        user_q = model.meta.Session.query(model.User)
        reviewers = [user_q.filter(model.User.id == reviewer_id).one() for reviewer_id in reviewer_ids]
        return reviewers
    
    def _get_username_from_id(self, id):
        if id == CONSENSUS_USER:
            return "consensus"
        user_q = model.meta.Session.query(model.User)
        return user_q.filter(model.User.id == id).one().username    
        
    def _get_id_from_username(self, username):
        user_q = model.meta.Session.query(model.User)
        return user_q.filter(model.User.username == username).one().id
        
    def _get_review_from_id(self, review_id):
        review_q = model.meta.Session.query(model.Review)
        return review_q.filter(model.Review.review_id == review_id).one()
        
    def _get_citations_for_review(self, review_id):
        citation_q = model.meta.Session.query(model.Citation)
        citations_for_review = citation_q.filter(model.Citation.review_id == review_id).all()
        return citations_for_review
        
    def _get_citation_from_id(self, citation_id):
        citation_q = model.meta.Session.query(model.Citation)
        return citation_q.filter(model.Citation.citation_id == citation_id).one()
        
    def _get_assignment_from_id(self, assignment_id):
        assignment_q = model.meta.Session.query(model.Assignment)
        try:
            return assignment_q.filter(model.Assignment.id == assignment_id).one()
        except:
            pdb.set_trace()

        
    def _create_perpetual_task_for_review(self, review_id):
        new_task = model.Task()
        new_task.review = review_id
        new_task.task_type = u"perpetual"
        new_task.num_assigned = -1 # this is meaningless for `perpetual' assignments
        model.Session.add(new_task)
        model.Session.commit()
        
    def _create_initial_task_for_review(self, review_id, n):
        '''
        picks a random set of the citations from the specified review and 
        adds them into the FixedTask table -- participants in this 
        review should subsequently be tasked with Assignments that reference this.
        '''
        # first grab some ids at random
        init_ids = random.sample(\
                [citation.citation_id for citation in self._get_citations_for_review(review_id)], n)
        # create an entry in the Assignments table
        init_task = model.Task()
        init_task.task_type = "initial"
        init_task.review = review_id
        init_task.num_assigned = n
        model.Session.add(init_task)
        model.Session.commit()
        
        # now associate the initial ids drawn
        # with this Task
        for citation_id in init_ids:
            fixed_task_entry = model.FixedTask()
            fixed_task_entry.task_id = init_task.id
            fixed_task_entry.citation_id = citation_id
            model.Session.add(fixed_task_entry)
            model.Session.commit()
            
            # need also to remove this id from the priority queue
            priority_q = model.meta.Session.query(model.Priority)
            priority_entry = priority_q.filter(model.Priority.citation_id == citation_id).one()
            model.Session.delete(priority_entry)
            model.Session.commit()
            
    def _assign_initial_tasks(self, user_id, review_id):
        task_q = model.meta.Session.query(model.Task)
        initial_tasks_for_review = task_q.filter(and_(\
                        model.Task.review == review_id,
                        model.Task.task_type == u"initial"
                    )).all()
        
        for task in initial_tasks_for_review:
            self._assign_task(user_id, task, review_id)

    def _assign_perpetual_task(self, user_id, review_id):
        '''
        If there is a perpetual task associated with the
        given review, it assigns it to user_id.
        '''
        task_q = model.meta.Session.query(model.Task)

        perpetual_tasks_for_review = task_q.filter(and_(\
                        model.Task.review == review_id,
                        model.Task.task_type == u"perpetual"
                    )).all()
      
       
        if len(perpetual_tasks_for_review) > 0:
            # note that we assume there's only *one* perpetual 
            # task per review, or in any case we ignore any
            # others.
            self._assign_task(user_id, perpetual_tasks_for_review[0], \
                                review_id)
            
        
    def _assign_task(self, user_id, task, review_id, due_date=None):
        assignment = model.Assignment()
        assignment.review_id = review_id
        assignment.reviewer_id = user_id
        assignment.task_id = task.id
        if due_date is not None:
            assignment.due_date = due_date
        assignment.date_assigned = datetime.datetime.now()
        assignment.done_so_far = 0
        ##
        # note that we keep these two fields 
        # in the assignment table, even though
        # they are redundant with the entries in
        # the task table. we do this for convienence. 
        assignment.num_assigned = task.num_assigned
        assignment.assignment_type = task.task_type

        model.Session.add(assignment)
        model.Session.commit()
            
    def _mark_up_citation(self, review_id, citation):
        # pull the labeled terms for this review
        labeled_term_q = model.meta.Session.query(model.LabeledFeature)
        reviewer_id = request.environ.get('repoze.who.identity')['user'].id
        labeled_terms = labeled_term_q.filter(and_(\
                            model.LabeledFeature.review_id == review_id,\
                            model.LabeledFeature.reviewer_id == reviewer_id)).all()
        citation.marked_up_title = citation.title
        citation.marked_up_abstract = citation.abstract
        
        # sort the labeled terms by length (inverse)
        labeled_terms.sort(cmp=lambda x,y: len(y.term) - len(x.term))
        
        # strip these to sanitize input to RE.
        # note that this means users cannot provide REs
        # themselves.
        meta_chars = ". ^ $ * + ? { } [ ] \ | ( )".split(" ")
        for term in labeled_terms:
            # 'sanitize' the string, i.e., escape special chars
            term_text = []
            for x in term.term:
                if x in meta_chars:
                    term_text.append("\%s"%x)
                else:
                    term_text.append(x)
                    
            #term_text = "".join([x for x in term.term if not x in meta_chars else "\%s"%x])
            term_text = "".join(term_text)
            term_re = re.compile(term_text, re.IGNORECASE)
            
            # (case-insensitive) replace the term in the title text
            citation.marked_up_title = term_re.sub(\
                        "<font color='%s'>%s</font>" % (COLOR_D[term.label], term.term),\
                        citation.marked_up_title)
                  
            if citation.marked_up_abstract is not None:      
                # ... and in the abstract text
                citation.marked_up_abstract = term_re.sub(\
                            "<font color='%s'>%s</font>" % (COLOR_D[term.label], term.term),\
                            citation.marked_up_abstract)
                        
            else:
                citation.marked_up_abstract = ""
        citation.marked_up_title = literal(citation.marked_up_title)
        citation.marked_up_abstract = literal(citation.marked_up_abstract)
        return citation
   
        


