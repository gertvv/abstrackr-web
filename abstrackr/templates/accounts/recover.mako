<%inherit file="../site.mako" />
<%def name="title()">recover password</%def>


<div class="content">

Forgot your password, huh? tsk, tsk. <br/><br/>
Enter your email below and we'll send you instructions to reset it. (Make sure to check your spam folder for the email.)
<br/><br/>
    <center>

<table class="form_table">
 ${h.form(url(controller='account', action='reset_password'))}
    <tr><td><label>your email:</td> <td>${h.text('email')}</label></td></tr>
    <tr><td></td><td>${h.submit('post', 'reset my password!')}</td></tr>
  ${h.end_form()}
  </table>
  </center>
</div>