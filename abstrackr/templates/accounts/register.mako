<%inherit file="../site.mako" />
<%def name="title()">register</%def>


<div class="content">
    <center>
    
<table class="form_table">
 ${h.form(url(controller='account', action='create_account_handler'))}
    <tr><td><label>first name:</td> <td>${h.text('first name')}</label></td></tr>
    <tr><td><label>last name:</td> <td>${h.text('last name')}</label></td></tr>
    <tr><td><label>email:</td> <td>${h.text('email')}</label></td></tr>
    <tr><td><label>username:</td> <td>${h.text('username')}</label></td></tr>
    <tr><td><label>password:</td> <td>${h.text('password', type='password')}</label></td></tr>
    <tr><td></td><td>${h.submit('post', 'sign me up!')}</td></tr>
  ${h.end_form()}
  </table>
  </center>
</div>