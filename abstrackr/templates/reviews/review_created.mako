
<%inherit file="../site.mako" />
<%def name="title()">${c.review.name}</%def>
<script language="JavaScript">
    var cal = new CalendarPopup();
</script>

<script language="javascript">
jQuery(document).ready(function(){
    jQuery("#submit").click(function(){
        $("#okay_div").fadeIn(2000)
    });
});
</script>

<div class="breadcrumbs">
<a href="${url(controller='account', action='welcome')}">./dashboard</a>/<a href="${url(controller='review', action='show_review', id=c.review.review_id)}">${c.review.name}</a>
</div>


<h1>Your review (${c.review.name}) has been succesfully created!</h1>

<div class="content">

Awesome, you're ready to start screening.

<br/><br/>
<b>What now?</b>, you ask. You can invite additional reviewers, if you'd like.<br/><br/>

<div align="right">
<form action = "/review/invite_reviewers/${c.review.review_id}">
<div class="actions">
<label for="emails">Enter their emails (comma-separated).</label>
<input type="text" id="emails" name="emails" /><br />
<input type="submit" id="submit" value="invite them!" />
</div>
</form>
    <div class="loading" id="okay_div">
        okay! emails have been sent!
    </div>
</div>

<br/><br/>
Or, send this link directly to participants:

<center>
<h2><a href="http://abstrackr.tuftscaes.org/join/${c.review.code}">http://abstrackr.tuftscaes.org/join/${c.review.code}</a></h2>
</center>


</div>