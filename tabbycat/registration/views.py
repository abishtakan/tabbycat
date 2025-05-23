from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.forms import SimpleArrayField
from django.db.models import Prefetch
from django.forms import modelformset_factory
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _, gettext_lazy, ngettext
from django.views.generic.edit import CreateView
from formtools.wizard.views import SessionWizardView

from actionlog.mixins import LogActionMixin
from actionlog.models import ActionLogEntry
from participants.models import Coach, Speaker, TournamentInstitution
from privateurls.utils import populate_url_keys
from tournaments.mixins import PublicTournamentPageMixin, TournamentMixin
from users.permissions import Permission
from utils.misc import reverse_tournament
from utils.mixins import AdministratorMixin
from utils.tables import TabbycatTableBuilder
from utils.views import ModelFormSetView, VueTableTemplateView

from .forms import AdjudicatorForm, InstitutionCoachForm, SpeakerForm, TeamForm, TournamentInstitutionForm
from .models import Question


class CustomQuestionFormMixin:

    def get_form_kwargs(self, step=None):
        if step is not None:
            kwargs = super().get_form_kwargs(step)
        else:
            kwargs = super().get_form_kwargs()
        kwargs['tournament'] = self.tournament
        return kwargs


class InstitutionalRegistrationMixin:

    def get_institution(self):
        ti = TournamentInstitution.objects.filter(tournament=self.tournament, coach__url_key=self.kwargs['url_key']).select_related('institution')
        return get_object_or_404(ti).institution

    @property
    def institution(self):
        if not hasattr(self, '_institution'):
            self._institution = self.get_institution()
        return self._institution

    def get_form_kwargs(self, step=None):
        if step is not None:
            kwargs = super().get_form_kwargs(step)
        else:
            kwargs = super().get_form_kwargs()
        kwargs['institution'] = self.institution
        return kwargs

    def get_success_url(self):
        return reverse_tournament('reg-inst-landing', self.tournament, kwargs={'url_key': self.kwargs['url_key']})


class CreateInstitutionFormView(LogActionMixin, PublicTournamentPageMixin, CustomQuestionFormMixin, SessionWizardView):
    form_list = [
        ('institution', TournamentInstitutionForm),
        ('coach', InstitutionCoachForm),
    ]
    template_name = 'wizard_registration_form.html'
    page_emoji = '🏫'
    page_title = gettext_lazy("Register Institution")

    public_page_preference = 'institution_registration'
    action_log_type = ActionLogEntry.ActionType.INSTITUTION_REGISTER

    def get_success_url(self, coach):
        return reverse_tournament('reg-inst-landing', self.tournament, kwargs={'url_key': coach.url_key})

    def done(self, form_list, form_dict, **kwargs):
        t_inst = form_dict['institution'].save()
        self.object = t_inst

        coach_form = form_dict['coach']
        coach_form.instance.tournament_institution = t_inst
        coach = coach_form.save()
        populate_url_keys(coach)

        messages.success(self.request, _("Your institution %s has been registered!") % t_inst.institution.name)
        self.log_action()
        return HttpResponseRedirect(self.get_success_url(coach))


class BaseCreateTeamFormView(LogActionMixin, PublicTournamentPageMixin, CustomQuestionFormMixin, SessionWizardView):
    form_list = [
        ('team', TeamForm),
        ('speaker', modelformset_factory(Speaker, form=SpeakerForm, extra=0)),
    ]
    template_name = 'wizard_registration_form.html'
    page_emoji = '👯'

    public_page_preference = 'open_team_registration'
    action_log_type = ActionLogEntry.ActionType.TEAM_REGISTER

    def get_page_title(self):
        match self.steps.current:
            case 'team':
                return _("Register Team")
            case 'speaker':
                return ngettext('Register Speaker', 'Register Speakers', self.tournament.pref('speakers_in_team'))
        return ''

    def get_page_subtitle(self):
        if self.steps.current == 'speaker':
            team_form = self.get_form(
                self.steps.first,
                data=self.storage.get_step_data(self.steps.first),
            )
            if team_form.is_valid():
                team = team_form.instance
                team.tournament = self.tournament
                team.institution = self.institution
                return _("for %s") % team._construct_short_name()
        return ''

    def get_form_kwargs(self, step=None):
        kwargs = super().get_form_kwargs(step)
        if step == 'speaker':
            kwargs.update({'queryset': self.get_speaker_queryset(), 'form_kwargs': {'tournament': self.tournament}})
            kwargs.pop('tournament')
        return kwargs

    def get_speaker_queryset(self):
        return Speaker.objects.none()

    def get_success_url(self):
        return reverse_tournament('tournament-public-index', self.tournament)

    def get_form(self, step=None, data=None, files=None):
        form = super().get_form(step, data, files)

        if step == 'speaker':
            form.extra = self.tournament.pref('speakers_in_team')
        return form

    def done(self, form_list, form_dict, **kwargs):
        team = form_dict['team'].save()
        self.object = team

        for speaker in form_dict['speaker']:
            speaker.instance.team = team
            speaker.save()

        messages.success(self.request, _("Your team %s has been registered!") % team.short_name)
        self.log_action()
        return HttpResponseRedirect(self.get_success_url())


class BaseCreateAdjudicatorFormView(LogActionMixin, PublicTournamentPageMixin, CustomQuestionFormMixin, CreateView):
    form_class = AdjudicatorForm
    template_name = 'adjudicator_registration_form.html'
    page_emoji = '👂'
    page_title = gettext_lazy("Register Adjudicator")

    public_page_preference = 'open_adj_registration'
    action_log_type = ActionLogEntry.ActionType.ADJUDICATOR_REGISTER

    def get_success_url(self):
        return reverse_tournament('tournament-public-index', self.tournament)

    def form_valid(self, form):
        obj = form.save()
        messages.success(self.request, _("Your adjudicator %s has been registered!") % obj.name)
        return super().form_valid(form)


class CreateSpeakerFormView(TournamentMixin, CustomQuestionFormMixin, CreateView):
    form_class = SpeakerForm
    template_name = 'adjudicator_registration_form.html'
    page_emoji = '👄'
    page_title = gettext_lazy("Register Speaker")

    @property
    def team(self):
        return self.tournament.team_set.get(pk=self.request.kwargs['pk'])

    def get_page_subtitle(self):
        return ""


class InstitutionalLandingPageView(TournamentMixin, InstitutionalRegistrationMixin, VueTableTemplateView):

    template_name = 'coach_private_url.html'

    def get_adj_table(self):
        adjudicators = self.tournament.adjudicator_set.filter(institution=self.institution)

        table = TabbycatTableBuilder(view=self, title=_('Adjudicators'), sort_key='name')
        table.add_adjudicator_columns(adjudicators, show_institutions=False, show_metadata=False)

        return table

    def get_team_table(self):
        teams = self.tournament.team_set.filter(institution=self.institution)
        table = TabbycatTableBuilder(view=self, title=_('Teams'), sort_key='name')
        table.add_team_columns(teams)

        return table

    def get_tables(self):
        return [self.get_adj_table(), self.get_team_table()]

    def get_context_data(self, **kwargs):
        kwargs["coach"] = get_object_or_404(Coach, tournament_institution__tournament=self.tournament, url_key=kwargs['url_key'])
        kwargs["institution"] = self.institution
        return super().get_context_data(**kwargs)


class InstitutionalCreateTeamFormView(InstitutionalRegistrationMixin, BaseCreateTeamFormView):

    public_page_preference = 'institution_participant_registration'

    def get_page_subtitle(self):
        if self.steps.current == 'team':
            return _("from %s") % self.institution.name
        return super().get_page_subtitle()

    def get_form_kwargs(self, step=None):
        kwargs = super().get_form_kwargs(step)
        if step == 'speaker':
            kwargs.pop('institution')
        return kwargs


class InstitutionalCreateAdjudicatorFormView(InstitutionalRegistrationMixin, BaseCreateAdjudicatorFormView):

    public_page_preference = 'institution_participant_registration'

    def get_page_subtitle(self):
        return _("from %s") % self.institution.name


def handle_question_columns(table: TabbycatTableBuilder, objects, questions=None, suffix=0) -> None:
    if questions is None:
        questions = table.tournament.question_set.filter(for_content_type=ContentType.objects.get_for_model(objects.model)).order_by('seq')
    question_columns = {q: [] for q in questions}

    for obj in objects:
        obj_answers = {answer.question: answer.answer for answer in obj.answers.all()}
        for question, answers in question_columns.items():
            answers.append(obj_answers.get(question, ''))

    for question, answers in question_columns.items():
        table.add_column({'key': f'cq-{question.pk}-{suffix}', 'title': question.name}, answers)


class InstitutionRegistrationTableView(TournamentMixin, AdministratorMixin, VueTableTemplateView):
    page_emoji = '🏫'
    page_title = gettext_lazy("Institutional Registration")

    view_permission = Permission.VIEW_REGISTRATION

    def get_table(self):
        t_institutions = self.tournament.tournamentinstitution_set.select_related('institution').prefetch_related(
            'answers__question',
        ).all()

        table = TabbycatTableBuilder(view=self, title=_('Responses'), sort_key='name')
        table.add_column({'key': 'name', 'title': _("Name")}, [t_inst.institution.name for t_inst in t_institutions])
        table.add_column({'key': 'teams_requested', 'title': _("Teams Requested")}, [
            {'text': t_inst.teams_requested, 'sort': t_inst.teams_requested} for t_inst in t_institutions
        ])
        table.add_column({'key': 'teams_allocated', 'title': _("Teams Allocated")}, [
            {'text': t_inst.teams_allocated, 'sort': t_inst.teams_allocated} for t_inst in t_institutions
        ])
        table.add_column({'key': 'adjudicators_requested', 'title': _("Adjudicators Requested")}, [
            {'text': t_inst.adjudicators_requested, 'sort': t_inst.adjudicators_requested} for t_inst in t_institutions
        ])
        table.add_column({'key': 'adjudicators_allocated', 'title': _("Adjudicators Allocated")}, [
            {'text': t_inst.adjudicators_allocated, 'sort': t_inst.adjudicators_allocated} for t_inst in t_institutions
        ])

        handle_question_columns(table, t_institutions)

        return table


class TeamRegistrationTableView(TournamentMixin, AdministratorMixin, VueTableTemplateView):
    page_emoji = '👯'
    page_title = gettext_lazy("Team Registration")

    view_permission = Permission.VIEW_REGISTRATION

    def get_table(self):
        def get_speaker(team, i):
            try:
                return team.speakers[i]
            except IndexError:
                return Speaker()

        teams = self.tournament.team_set.select_related('institution').prefetch_related(
            'answers__question',
            Prefetch('speaker_set', queryset=Speaker.objects.prefetch_related('answers__question')),
        ).all()
        spk_questions = self.tournament.question_set.filter(for_content_type=ContentType.objects.get_for_model(Speaker)).order_by('seq')

        table = TabbycatTableBuilder(view=self, title=_('Responses'), sort_key='team')
        table.add_team_columns(teams)

        handle_question_columns(table, teams)

        for i in range(self.tournament.pref('speakers_in_team')):
            table.add_column({'key': 'spk-%d' % i, 'title': _("Speaker %d") % (i+1,)}, [get_speaker(team, i).name for team in teams])
            table.add_column({'key': 'email-%d' % i, 'title': _("Email")}, [get_speaker(team, i).email for team in teams])

            handle_question_columns(table, [get_speaker(team, i) for team in teams], questions=spk_questions, suffix=i)

        return table


class AdjudicatorRegistrationTableView(TournamentMixin, AdministratorMixin, VueTableTemplateView):
    page_emoji = '👂'
    page_title = gettext_lazy("Adjudicator Registration")

    view_permission = Permission.VIEW_REGISTRATION

    def get_table(self):
        adjudicators = self.tournament.adjudicator_set.select_related('institution').prefetch_related('answers__question').all()

        table = TabbycatTableBuilder(view=self, title=_('Responses'), sort_key='name')
        table.add_adjudicator_columns(adjudicators, show_metadata=False)
        table.add_column({'key': 'email', 'title': _("Email")}, [adj.email for adj in adjudicators])

        handle_question_columns(table, adjudicators)

        return table


class CustomQuestionFormsetView(TournamentMixin, AdministratorMixin, ModelFormSetView):
    formset_model = Question
    formset_factory_kwargs = {
        'fields': ['name', 'text', 'help_text', 'answer_type', 'required', 'min_value', 'max_value', 'choices'],
        'field_classes': {'choices': SimpleArrayField},
    }
    question_model = None
    template_name = 'venue_categories_edit.html'

    view_permission = True
    edit_permission = Permission.EDIT_QUESTIONS

    page_emoji = '❓'
    page_title = gettext_lazy("Custom Questions")

    def get_page_subtitle(self):
        return _("for %s") % self.question_model._meta.verbose_name_plural

    def get_formset_queryset(self):
        return super().get_formset_queryset().filter(for_content_type=ContentType.objects.get_for_model(self.question_model)).order_by('seq')
